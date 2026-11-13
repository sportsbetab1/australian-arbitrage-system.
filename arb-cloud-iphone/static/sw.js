self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) {}
  const title = typeof data.title === 'string' ? data.title.slice(0, 120) : 'Arbitrage alert';
  const body = typeof data.body === 'string'
    ? data.body.slice(0, 240)
    : 'An arbitrage control notification is available.';
  event.waitUntil(self.registration.showNotification(title, {
    body,
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    tag: data.event_type && data.position_id
      ? String(data.event_type) + '-' + String(data.position_id)
      : undefined,
    data: { url: '/' }
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    const existing = windows.find(client => {
      try { return new URL(client.url).origin === self.location.origin; }
      catch (e) { return false; }
    });
    if (existing) {
      await existing.focus();
      return;
    }
    await clients.openWindow('/');
  })());
});
