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

  const statusLabels = {
    pending: "Pending",
    confirmed: "Confirmed",
    processing: "Processing",
    shipped: "Shipped",
    delivered: "Delivered",
    cancelled: "Cancelled"
  };

  messaging.onBackgroundMessage(function (payload) {
    const data = payload.data || {};
    const orderId = String(data.order_id || "");
    if (!/^\d+$/.test(orderId)) {
      return;
    }

    let title;
    let body;
    let url;
    let tag;
    if (data.type === "new_order") {
      title = data.title || "New customer order";
      body = data.body || "A new order has been placed.";
      url = "/admin/order?id=" + encodeURIComponent(orderId);
      tag = "admin-order-" + orderId;
    } else if (
      data.type === "order_status" &&
      statusLabels[data.status]
    ) {
      const orderNumber = String(data.order_no || "").slice(0, 40);
      title = "Order update";
      body = orderNumber
        ? "Order " + orderNumber + " is now " + statusLabels[data.status] + "."
        : "Your order is now " + statusLabels[data.status] + ".";
      url = "/order?id=" + encodeURIComponent(orderId);
      tag = "customer-order-" + orderId;
    } else {
      return;
    }

    self.registration.showNotification(
      title,
      {
        body,
        icon: "/favicon-48x48.png",
        badge: "/favicon-48x48.png",
        tag,
        renotify: false,
        data: { url }
      }
    );
  });

  self.addEventListener("notificationclick", function (event) {
    event.notification.close();

    const path = event.notification.data?.url;
    if (
      typeof path !== "string" ||
      !/^\/(?:admin\/order|order)\?id=\d+$/.test(path)
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
