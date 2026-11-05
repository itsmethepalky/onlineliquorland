(function () {
  "use strict";

  const DEVICE_ID_KEY = "oll-admin-push-device-id";
  const FIREBASE_VERSION = "10.12.2";
  let firebaseMessaging = null;
  let foregroundListener = null;
  let configPromise = null;
  let deviceId = null;
  let notificationsEnabled = false;
  let refreshInProgress = false;

  function element(id) {
    return document.getElementById(id);
  }

  function setStatus(message) {
    element("adminPushStatus").textContent = message;
  }

  function showMessage(id, message) {
    const box = element(id);
    box.textContent = message;
    box.classList.remove("hidden");
  }

  function hideMessage(id) {
    element(id).classList.add("hidden");
  }

  function getDeviceId() {
    const storedId = window.localStorage.getItem(DEVICE_ID_KEY);
    if (storedId) {
      return storedId;
    }

    if (!window.crypto?.randomUUID) {
      throw new Error("This browser cannot create a secure device ID.");
    }

    const newId = window.crypto.randomUUID();
    window.localStorage.setItem(DEVICE_ID_KEY, newId);
    return newId;
  }

  async function loadConfig() {
    if (!configPromise) {
      configPromise = fetch("/api/firebase-messaging-config", {
        headers: { Accept: "application/json" },
        cache: "no-store"
      }).then(async function (response) {
        if (!response.ok) {
          throw new Error("Firebase browser configuration is unavailable.");
        }

        const config = await response.json();
        if (
          !config.configured ||
          !config.firebaseConfig ||
          !config.vapidKey
        ) {
          throw new Error(
            "Firebase Web Push is not configured on the server."
          );
        }
        return config;
      });
    }
    return configPromise;
  }

  function loadScript(source) {
    return new Promise(function (resolve, reject) {
      const script = document.createElement("script");
      script.src = source;
      script.async = false;
      script.onload = resolve;
      script.onerror = function () {
        reject(new Error("Could not load Firebase Messaging."));
      };
      document.head.appendChild(script);
    });
  }

  async function getMessaging() {
    if (firebaseMessaging) {
      return firebaseMessaging;
    }

    const config = await loadConfig();
    if (!window.firebase?.apps?.length) {
      if (!window.firebase) {
        await loadScript(
          `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-app-compat.js`
        );
      }

      if (!window.firebase?.messaging) {
        await loadScript(
          `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-messaging-compat.js`
        );
      }

      if (!window.firebase?.apps?.length) {
        window.firebase.initializeApp(config.firebaseConfig);
      }
    } else if (!window.firebase.messaging) {
      await loadScript(
        `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}/firebase-messaging-compat.js`
      );
    }

    if (!(await window.firebase.messaging.isSupported())) {
      throw new Error("Firebase push notifications are not supported here.");
    }

    const registration = await navigator.serviceWorker.register(
      "/firebase-messaging-sw.js",
      { scope: "/" }
    );
    await navigator.serviceWorker.ready;

    firebaseMessaging = window.firebase.messaging();
    firebaseMessaging.__serviceWorkerRegistration = registration;

    if (!foregroundListener) {
      foregroundListener = firebaseMessaging.onMessage(function (payload) {
        const data = payload.data || {};
        if (data.type !== "new_order" || !data.order_id) {
          return;
        }

        const notification = new Notification(
          data.title || "New customer order",
          {
            body: data.body || "A new order has been placed.",
            icon: "/favicon-48x48.png",
            tag: `admin-order-${data.order_id}`
          }
        );
        notification.addEventListener("click", function () {
          window.focus();
          window.location.assign(
            `/admin/order?id=${encodeURIComponent(data.order_id)}`
          );
        });
      });
    }

    return firebaseMessaging;
  }

  async function refreshTokenRegistration(messaging) {
    const token = await messaging.getToken({
      vapidKey: (await loadConfig()).vapidKey,
      serviceWorkerRegistration:
        messaging.__serviceWorkerRegistration
    });

    if (!token) {
      throw new Error("Firebase did not return a browser push token.");
    }

    const response = await window.adminFetch("/api/admin/push-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token,
        device_id: deviceId,
        platform: "web"
      })
    });

    if (!response.ok) {
      const result = await response.json().catch(function () {
        return {};
      });
      throw new Error(
        result.detail || "Could not register this browser for order alerts."
      );
    }
    notificationsEnabled = true;
  }

  async function refreshActiveToken() {
    if (
      !notificationsEnabled ||
      !firebaseMessaging ||
      refreshInProgress ||
      document.visibilityState !== "visible"
    ) {
      return;
    }

    refreshInProgress = true;
    try {
      await refreshTokenRegistration(firebaseMessaging);
    } catch (error) {
      setStatus("Could not refresh this browser's alert registration.");
      showMessage("adminPushError", error.message);
    } finally {
      refreshInProgress = false;
    }
  }

  async function updateStatus() {
    if (!("Notification" in window) || !("serviceWorker" in navigator)) {
      setStatus("This browser does not support web push.");
      element("enableAdminPush").disabled = true;
      return;
    }

    try {
      await loadConfig();
    } catch (error) {
      setStatus("Push notifications are not configured.");
      element("enableAdminPush").disabled = true;
      showMessage("adminPushError", error.message);
      return;
    }

    if (Notification.permission === "denied") {
      setStatus("Notifications are blocked in browser settings.");
      element("enableAdminPush").disabled = true;
      return;
    }

    const response = await window.adminFetch(
      `/api/admin/push-token?device_id=${encodeURIComponent(deviceId)}`
    );
    if (!response.ok) {
      throw new Error("Could not check this browser's alert settings.");
    }

    const state = await response.json();
    if (!state.enabled) {
      setStatus("Not enabled on this browser.");
      element("enableAdminPush").classList.remove("hidden");
      element("disableAdminPush").classList.add("hidden");
      return;
    }

    if (Notification.permission !== "granted") {
      setStatus("Enabled, but browser permission is not granted.");
      element("enableAdminPush").classList.remove("hidden");
    } else {
      const messaging = await getMessaging();
      await refreshTokenRegistration(messaging);
      setStatus("Order alerts are enabled on this browser.");
      element("enableAdminPush").classList.add("hidden");
    }

    element("disableAdminPush").classList.remove("hidden");
  }

  async function enableNotifications() {
    hideMessage("adminPushError");
    hideMessage("adminPushSuccess");

    try {
      if (!("Notification" in window) || !("serviceWorker" in navigator)) {
        throw new Error("This browser does not support web push.");
      }

      const permission = Notification.permission === "granted"
        ? "granted"
        : await Notification.requestPermission();
      if (permission !== "granted") {
        throw new Error(
          "Allow notifications in your browser to enable order alerts."
        );
      }

      const messaging = await getMessaging();
      await refreshTokenRegistration(messaging);
      setStatus("Order alerts are enabled on this browser.");
      element("enableAdminPush").classList.add("hidden");
      element("disableAdminPush").classList.remove("hidden");
      showMessage(
        "adminPushSuccess",
        "This browser is enrolled for admin order alerts, including after sign-out."
      );
    } catch (error) {
      setStatus("Could not enable order alerts.");
      showMessage("adminPushError", error.message);
    }
  }

  async function disableNotifications() {
    hideMessage("adminPushError");
    hideMessage("adminPushSuccess");
    element("disableAdminPush").disabled = true;

    try {
      const response = await window.adminFetch(
        "/api/admin/push-token/remove",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device_id: deviceId })
        }
      );
      if (!response.ok) {
        const result = await response.json().catch(function () {
          return {};
        });
        throw new Error(
          result.detail || "Could not turn off this browser's order alerts."
        );
      }

      notificationsEnabled = false;
      if (firebaseMessaging) {
        const deleted = await firebaseMessaging.deleteToken();
        if (!deleted) {
          throw new Error(
            "Alerts were removed from the server, but this browser token could not be cleared."
          );
        }
      }

      setStatus("Order alerts are off on this browser.");
      element("disableAdminPush").classList.add("hidden");
      element("enableAdminPush").classList.remove("hidden");
      showMessage("adminPushSuccess", "This browser has been unenrolled.");
    } catch (error) {
      setStatus("Could not turn off order alerts.");
      showMessage("adminPushError", error.message);
    } finally {
      element("disableAdminPush").disabled = false;
    }
  }

  async function initialize() {
    element("enableAdminPush")
      .addEventListener("click", enableNotifications);
    element("disableAdminPush")
      .addEventListener("click", disableNotifications);
    window.addEventListener("focus", refreshActiveToken);
    document.addEventListener("visibilitychange", refreshActiveToken);
    window.setInterval(refreshActiveToken, 12 * 60 * 60 * 1000);

    try {
      deviceId = getDeviceId();
      await updateStatus();
    } catch (error) {
      setStatus("Notification settings could not be loaded.");
      showMessage("adminPushError", error.message);
    }
  }

  window.initializeAdminPush = initialize;
})();
