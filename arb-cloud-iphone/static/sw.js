self.addEventListener('push', event => {
  let data = {};
  try { data = event.data.json(); } catch(e) {}
  event.waitUntil(self.registration.showNotification(data.title || 'Arbitrage alert', {
    body: data.body || 'A new candidate has been detected.',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png'
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
