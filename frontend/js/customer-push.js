(function () {
  "use strict";

  const DEVICE_ID_KEY = "oll-customer-push-device-id";
  const ENABLED_KEY_PREFIX = "oll-customer-push-enabled:";
  const FIREBASE_VERSION = "10.12.2";
  const STATUS_LABELS = {
    pending: "Pending",
    confirmed: "Confirmed",
    processing: "Processing",
    shipped: "Shipped",
    delivered: "Delivered",
    cancelled: "Cancelled"
  };

  let userId = "";
  let deviceId = "";
  let firebaseMessaging = null;
  let serviceWorkerRegistration = null;
  let configPromise = null;
  let foregroundListener = null;
  let notificationsEnabled = false;
  let refreshInProgress = false;
  let initialized = false;

  function element(id) {
    return document.getElementById(id);
  }

  function setStatus(message) {
    element("customerPushStatus").textContent = message;
  }

  function setMessage(message, isError) {
    const messageElement = element("customerPushMessage");
    messageElement.textContent = message;
    messageElement.classList.toggle("error", Boolean(isError));
    messageElement.classList.toggle("good", Boolean(message) && !isError);
  }

  function updateButtons() {
    element("enableCustomerPush").classList.toggle(
      "hidden",
      notificationsEnabled
    );
    element("disableCustomerPush").classList.toggle(
      "hidden",
      !notificationsEnabled
    );
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
        cache: "no-store"
      }).then(async function (response) {
        if (!response.ok) {
          throw new Error("Could not load push notification settings.");
        }

        const config = await response.json();
        if (
          !config.configured ||
          !config.firebaseConfig ||
          !config.vapidKey
        ) {
          throw new Error("Push notifications are not configured.");
        }
        return config;
      }).catch(function (error) {
        configPromise = null;
        throw error;
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

    serviceWorkerRegistration = await navigator.serviceWorker.register(
      "/firebase-messaging-sw.js",
      { scope: "/" }
    );
    await navigator.serviceWorker.ready;
    firebaseMessaging = window.firebase.messaging();

    if (!foregroundListener) {
      foregroundListener = firebaseMessaging.onMessage(function (payload) {
        const data = payload.data || {};
        const orderId = String(data.order_id || "");
        if (
          data.type !== "order_status" ||
          !/^\d+$/.test(orderId) ||
          !STATUS_LABELS[data.status]
        ) {
          return;
        }

        const orderNumber = String(data.order_no || "").slice(0, 40);
        const body = orderNumber
          ? `Order ${orderNumber} is now ${STATUS_LABELS[data.status]}.`
          : `Your order is now ${STATUS_LABELS[data.status]}.`;
        void serviceWorkerRegistration
          .showNotification("Order update", {
            body,
            icon: "/favicon-48x48.png",
            tag: `customer-order-${orderId}`,
            data: { url: `/order?id=${encodeURIComponent(orderId)}` }
          })
          .catch(function (error) {
            console.error("CUSTOMER PUSH NOTIFICATION ERROR:", error.name);
          });
      });
    }

    return firebaseMessaging;
  }

  async function getAccessToken() {
    const {
      data: { session },
      error
    } = await window.supabaseClient.auth.getSession();
    if (error) {
      throw error;
    }
    if (!session?.access_token || session.user?.id !== userId) {
      throw new Error("Please sign in again to manage order notifications.");
    }
    return session.access_token;
  }

  async function refreshTokenRegistration() {
    const messaging = await getMessaging();
    const config = await loadConfig();
    const token = await messaging.getToken({
      vapidKey: config.vapidKey,
      serviceWorkerRegistration
    });
    if (!token) {
      throw new Error("Firebase did not return a browser push token.");
    }

    const accessToken = await getAccessToken();
    const response = await fetch("/api/account/push-token", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ token, device_id: deviceId })
    });
    if (!response.ok) {
      const result = await response.json().catch(function () {
        return {};
      });
      throw new Error(
        result.detail || "Could not register this browser for order updates."
      );
    }
  }

  async function refreshActiveToken() {
    if (
      !notificationsEnabled ||
      refreshInProgress ||
      document.visibilityState !== "visible"
    ) {
      return;
    }

    refreshInProgress = true;
    try {
      await refreshTokenRegistration();
    } catch (error) {
      setMessage(error.message || "Could not refresh this browser's registration.", true);
    } finally {
      refreshInProgress = false;
    }
  }

  async function enableNotifications() {
    setMessage("", false);
    element("enableCustomerPush").disabled = true;
    try {
      const permission = Notification.permission === "granted"
        ? "granted"
        : await Notification.requestPermission();
      if (permission !== "granted") {
        throw new Error(
          "Allow notifications in your browser to receive order updates."
        );
      }

      await refreshTokenRegistration();
      window.localStorage.setItem(ENABLED_KEY_PREFIX + userId, "true");
      notificationsEnabled = true;
      updateButtons();
      setStatus("Order status notifications are enabled on this browser.");
      setMessage("This browser is enrolled for your order updates.", false);
    } catch (error) {
      setStatus("Could not enable order status notifications.");
      setMessage(error.message || "Could not enable order notifications.", true);
    } finally {
      element("enableCustomerPush").disabled = false;
    }
  }

  async function disableNotifications() {
    setMessage("", false);
    element("disableCustomerPush").disabled = true;
    try {
      const accessToken = await getAccessToken();
      const response = await fetch("/api/account/push-token/remove", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ device_id: deviceId })
      });
      if (!response.ok) {
        const result = await response.json().catch(function () {
          return {};
        });
        throw new Error(
          result.detail || "Could not disable order notifications."
        );
      }

      window.localStorage.removeItem(ENABLED_KEY_PREFIX + userId);
      notificationsEnabled = false;
      updateButtons();
      setStatus("Order status notifications are off on this browser.");
      setMessage("This browser has been unenrolled.", false);
    } catch (error) {
      setMessage(error.message || "Could not disable order notifications.", true);
    } finally {
      element("disableCustomerPush").disabled = false;
    }
  }

  async function initialize(customerUserId) {
    if (!customerUserId || initialized) {
      return;
    }
    initialized = true;
    userId = String(customerUserId);

    element("enableCustomerPush").addEventListener(
      "click",
      enableNotifications
    );
    element("disableCustomerPush").addEventListener(
      "click",
      disableNotifications
    );
    window.addEventListener("focus", refreshActiveToken);
    document.addEventListener("visibilitychange", refreshActiveToken);
    window.setInterval(refreshActiveToken, 12 * 60 * 60 * 1000);

    try {
      deviceId = getDeviceId();
      if (
        !("Notification" in window) ||
        !("serviceWorker" in navigator)
      ) {
        element("enableCustomerPush").disabled = true;
        setStatus("This browser does not support push notifications.");
        return;
      }

      notificationsEnabled =
        window.localStorage.getItem(ENABLED_KEY_PREFIX + userId) === "true";
      updateButtons();

      if (Notification.permission === "denied") {
        element("enableCustomerPush").disabled = true;
        setStatus("Notifications are blocked in browser settings.");
        if (notificationsEnabled) {
          setMessage(
            "Allow notifications in browser settings, or disable updates here.",
            true
          );
        }
        return;
      }

      if (!notificationsEnabled) {
        setStatus("Push notifications are not enabled on this browser.");
        return;
      }
      if (Notification.permission !== "granted") {
        element("enableCustomerPush").classList.remove("hidden");
        setStatus("Allow notifications in browser settings to resume updates.");
        return;
      }

      await refreshTokenRegistration();
      setStatus("Order status notifications are enabled on this browser.");
    } catch (error) {
      setStatus("Could not load order notification settings.");
      setMessage(error.message || "Could not initialize notifications.", true);
    }
  }

  window.initializeCustomerPush = initialize;
})();
