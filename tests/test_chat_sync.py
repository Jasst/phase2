# -*- coding: utf-8 -*-
"""Серверные чаты, синхронизация между устройствами и ротация MCP-токена."""
import json

from pulsenet.chat_store import ChatStore
from pulsenet.unified_mcp.auth import TokenStore

from tests.test_server_clean import _json, running  # noqa: F401 — общий фикстурный сервер


def _sess(sid, updated=1000, title='Чат'):
    return {'id': sid, 'chatId': sid, 'title': title, 'created': 900, 'updated': updated}


def _msg(mid, role, text, time=1000):
    return {'id': mid, 'role': role, 'text': text, 'time': time}


def test_chat_store_merge_snapshot_and_revision(tmp_path):
    store = ChatStore(str(tmp_path / 'chats.sqlite'))
    assert store.revision('u1') == 0
    rev = store.merge('u1', [_sess('c1')], {'c1': [_msg('m1', 'user', 'привет')]})
    assert rev > 0
    snap = store.snapshot('u1')
    assert snap['rev'] == rev
    assert snap['sessions'][0]['title'] == 'Чат'
    assert [m['text'] for m in snap['messages']['c1']] == ['привет']

    # Повторная синхронизация того же состояния не создаёт дубликатов.
    assert store.merge('u1', [_sess('c1')], {'c1': [_msg('m1', 'user', 'привет')]}) == rev
    assert len(store.snapshot('u1')['messages']['c1']) == 1

    # Могилка не даёт офлайн-устройству воскресить удалённый чат.
    store.delete('u1', 'c1')
    store.merge('u1', [_sess('c1', updated=1)], {'c1': [_msg('m1', 'user', 'привет')]})
    assert store.snapshot('u1')['sessions'] == []
    store.close()


def test_chat_store_append_survives_client_disconnect(tmp_path):
    store = ChatStore(str(tmp_path / 'chats.sqlite'))
    store.append('u2', 'c9', [_msg('a1', 'user', 'вопрос')], title='Вопрос')
    store.append('u2', 'c9', [_msg('a2', 'assistant', 'ответ')])
    snap = store.snapshot('u2')
    assert [m['role'] for m in snap['messages']['c9']] == ['user', 'assistant']
    assert snap['sessions'][0]['title'] == 'Вопрос'
    store.close()


def test_token_store_rotate_invalidates_old_and_can_display(tmp_path):
    store = TokenStore(str(tmp_path / 'tokens.sqlite'),
                       secret='test-secret', key_path=str(tmp_path / 'mcp.key'))
    old = store.issue('u7')
    assert store.verify(old) == 'u7'
    info = store.active('u7')
    assert info['displayable'] is True and info['token'] == old

    new = store.rotate('u7')
    assert new != old
    assert store.verify(old) is None
    assert store.verify(new) == 'u7'
    assert store.active('u7')['token'] == new
    assert store.revoke_user('u7') == 1
    assert store.active('u7') is None
    store.close()


def test_owner_token_cannot_be_rotated(tmp_path):
    store = TokenStore(str(tmp_path / 'tokens.sqlite'), owner_token='owner-secret',
                       secret='test-secret', key_path=str(tmp_path / 'mcp.key'))
    info = store.active('owner')
    assert info['owner'] is True and info['displayable'] is False and info['token'] == ''
    try:
        store.rotate('owner')
    except ValueError:
        pass
    else:
        raise AssertionError('токен владельца не должен меняться через API')
    store.close()


def test_chat_store_dedupes_reattached_answer_copies(tmp_path):
    """Старые копии одного ответа (разные id, один текст) не отдаются клиенту."""
    store = ChatStore(str(tmp_path / 'chats.sqlite'))
    store.merge('u1', [_sess('c1')], {'c1': [
        _msg('u1m', 'user', 'привет', time=1000),
        _msg('a1', 'assistant', 'один ответ', time=1001),
        _msg('a2', 'assistant', 'один ответ', time=1002),
        _msg('a3', 'assistant', '', time=1003),   # артефакт параллельного стрима
    ]})
    msgs = store.snapshot('u1')['messages']['c1']
    assert [m['id'] for m in msgs] == ['u1m', 'a1']


def test_save_chat_to_memory_builds_one_stable_document(running, monkeypatch):
    """Чат → память: один документ, системные сообщения выброшены, повтор идемпотентен."""
    app, port = running
    calls = []

    def fake_ingest(text, **kwargs):
        calls.append((text, kwargs))
        return {"status": "ok", "doc_id": "d:1", "facts": []}

    monkeypatch.setattr(app, "ingest", fake_ingest)
    body = {"chat_id": "phone", "messages": [
        {"id": "u1", "role": "user", "text": "привет", "time": 1000},
        {"id": "a1", "role": "assistant", "text": "здравствуй", "time": 1001.5},
        {"id": "s1", "role": "system", "text": "служебное", "time": 1002},
    ]}
    status, out = _json(port, "POST", "/api/chat/save-memory", body)
    assert status == 200
    assert out["messages"] == 2 and out["chars"] == len(calls[0][0])
    text, kw = calls[0]
    assert "Пользователь: привет" in text
    assert "Ассистент: здравствуй" in text
    assert "служебное" not in text
    assert kw["role"] == "chat" and kw["recorded_at"].endswith("+00:00")
    _json(port, "POST", "/api/chat/save-memory", body)
    assert calls[1][1]["recorded_at"] == kw["recorded_at"]

    status, cat = _json(port, "GET", "/api/tools")
    assert status == 200
    assert any(c["name"] == "chatmem" for c in cat["commands"])


def test_chat_active_exposes_message_ids():
    """Активная задача отдаёт id сообщений — второй клиент реаттачится, не дублируя."""
    from pulsenet import server

    job = server.ChatJob('phone', 'вопрос', uid='', raw_chat_id='phone',
                         user_msg_id='u-7', assistant_msg_id='a-7')

    class Handler:
        uid = None
        jobs = {'phone': job}
        sent = None

        def send_json(self, obj):
            self.sent = obj

    h = Handler()
    server._chat_active(h, None, {'chat_id': ['phone']})
    assert h.sent == {'active': True, 'chat_id': 'phone',
                      'user_msg_id': 'u-7', 'assistant_msg_id': 'a-7'}
    job.complete('ответ', 0, 'done')
    server._chat_active(h, None, {'chat_id': ['phone']})
    assert h.sent == {'active': False, 'chat_id': 'phone'}


def test_server_persists_answer_even_if_creator_disconnects(running):
    """Финальный ответ дописывается сервером: второе устройство получит его через /api/chats."""
    from tests.test_server_clean import _req
    app, port = running
    status, raw = _req(port, 'POST', '/api/chat', {
        'message': 'вопрос с телефона', 'chat_id': 'phone',
        'user_msg_id': 'u-1', 'assistant_msg_id': 'a-1'})
    assert status == 200 and b'[DONE]' in raw
    snap = _json(port, 'GET', '/api/chats')[1]
    msgs = snap['messages']['phone']
    assert [m['id'] for m in msgs] == ['u-1', 'a-1']
    assert msgs[0]['text'] == 'вопрос с телефона'
    assert 'пламя' in msgs[1]['text']


def test_server_chat_and_token_endpoints(running):
    app, port = running
    status, body = _json(port, 'PUT', '/api/chats',
                         {'sessions': [_sess('phone-chat')],
                          'messages': {'phone-chat': [_msg('p1', 'user', 'с телефона')]}})
    assert status == 200 and body['rev'] > 0
    status, body = _json(port, 'GET', '/api/chats')
    assert status == 200
    assert body['sessions'][0]['id'] == 'phone-chat'
    assert body['messages']['phone-chat'][0]['text'] == 'с телефона'
    rev = body['rev']
    assert _json(port, 'GET', '/api/chats?rev=%d' % rev)[1] == {'unchanged': True, 'rev': rev}

    status, body = _json(port, 'GET', '/api/mcp/token')
    assert status == 200 and body['can_rotate'] is True
    status, body = _json(port, 'POST', '/api/mcp/token', {})
    assert status == 200 and body['ok'] is True and len(body['token']) > 20
    status, info = _json(port, 'GET', '/api/mcp/token')
    assert status == 200 and info['active'] is True
    assert info['token'] == body['token']      # шифртекст в базе — токен виден на любом устройстве

    status, body = _json(port, 'DELETE', '/api/chats?chat_id=phone-chat')
    assert status == 200 and body['rev'] > rev
    assert _json(port, 'GET', '/api/chats')[1]['sessions'] == []
