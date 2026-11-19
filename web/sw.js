/* Устаревший service worker: чистит кэш и снимает регистрацию. */
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch (e) { /* кэш уже пуст или недоступен */ }
    await self.registration.unregister();
  })());
});
