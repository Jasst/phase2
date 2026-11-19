# Эксперименты

Отдельные инструменты, которые не входят в пакет `pulsenet`. Ядро они только читают
и вызывают публичный API; изменений в `pulsenet/` не требуют. Результаты лежат в
`experiments/out/` (в `.gitignore`), поэтому репозиторий не засоряется.

## Масштабирование (`scaling/`)

1. Сгенерировать пары из корпуса через учителя (LM Studio или llama-server):

   ```bat
   python experiments\scaling\generate_pairs.py --corpus corpus --url http://localhost:1234/v1/chat/completions --model имя-модели
   ```

   Пробный запуск на 20 предложениях: добавьте `--limit 20`. Повторный запуск дописывает
   только новые предложения.

2. Построить кривую обучения:

   ```bat
   python experiments\scaling\curve.py --pairs experiments\out\pairs.jsonl
   ```

   Результат: таблица в консоли и `experiments/out/curve.json`.

3. Проверить сами инструменты (без LLM):

   ```bat
   python -m pytest experiments\scaling\test_scaling.py
   ```

Эталонный ответ каждой пары — предложение из корпуса, а вопрос придумывает учитель. Так
измеряется обобщение сети, а не качество LLM.
