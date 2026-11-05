importScripts("/api/firebase-messaging-config.js");

if (self.firebaseMessagingPublicConfig?.configured) {
  importScripts(
    "https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js",
    "https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js"
  );

  firebase.initializeApp(
    self.firebaseMessagingPublicConfig.firebaseConfig
  );

  const messaging = firebase.messaging();

  messaging.onBackgroundMessage(function (payload) {
    const data = payload.data || {};
    if (data.type !== "new_order" || !data.order_id) {
      return;
    }

    self.registration.showNotification(
      data.title || "New customer order",
      {
        body: data.body || "A new order has been placed.",
        icon: "/favicon-48x48.png",
        badge: "/favicon-48x48.png",
        tag: "admin-order-" + data.order_id,
        renotify: false,
        data: {
          url: "/admin/order?id=" +
            encodeURIComponent(data.order_id)
        }
      }
    );
  });

  self.addEventListener("notificationclick", function (event) {
    event.notification.close();

    const path = event.notification.data?.url;
    if (
      typeof path !== "string" ||
      !path.startsWith("/admin/order?id=")
    ) {
      return;
    }

    const target = new URL(path, self.location.origin).href;
    event.waitUntil(
      self.clients.matchAll({
        type: "window",
        includeUncontrolled: true
      }).then(function (windows) {
        const existing = windows.find(function (client) {
          return new URL(client.url).origin === self.location.origin;
        });

        if (existing) {
          return existing.navigate(target).then(function () {
            return existing.focus();
          });
        }

        return self.clients.openWindow(target);
      })
    );
  });
}
