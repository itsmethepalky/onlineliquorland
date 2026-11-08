(function () {
  "use strict";

  const DEVICE_ID_KEY = "oll-admin-push-device-id";
  const SOUND_DATABASE = "oll-admin-push-settings";
  const SOUND_STORE = "settings";
  const SOUND_KEY = "order-sound";
  const MAX_SOUND_BYTES = 2 * 1024 * 1024;
  const FIREBASE_VERSION = "10.12.2";
  let firebaseMessaging = null;
  let foregroundListener = null;
  let configPromise = null;
  let deviceId = null;
  let customOrderSoundUrl = null;
  let activeOrderAudio = null;
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

  function openSoundDatabase() {
    return new Promise(function (resolve, reject) {
      const request = window.indexedDB.open(SOUND_DATABASE, 1);
      request.onupgradeneeded = function () {
        request.result.createObjectStore(SOUND_STORE);
      };
      request.onsuccess = function () {
        resolve(request.result);
      };
      request.onerror = function () {
        reject(request.error || new Error("Could not open sound settings."));
      };
    });
  }

  async function getCustomOrderSound() {
    const database = await openSoundDatabase();
    return new Promise(function (resolve, reject) {
      const transaction = database.transaction(SOUND_STORE, "readonly");
      const request = transaction.objectStore(SOUND_STORE).get(SOUND_KEY);
      request.onsuccess = function () {
        database.close();
        resolve(request.result || null);
      };
      request.onerror = function () {
        database.close();
        reject(request.error || new Error("Could not load custom sound."));
      };
    });
  }

  async function saveCustomOrderSound(file) {
    const database = await openSoundDatabase();
    return new Promise(function (resolve, reject) {
      const transaction = database.transaction(SOUND_STORE, "readwrite");
      transaction.objectStore(SOUND_STORE).put(file, SOUND_KEY);
      transaction.oncomplete = function () {
        database.close();
        resolve();
      };
      transaction.onerror = function () {
        database.close();
        reject(transaction.error || new Error("Could not save custom sound."));
      };
      transaction.onabort = function () {
        database.close();
        reject(transaction.error || new Error("Could not save custom sound."));
      };
    });
  }

  async function deleteCustomOrderSound() {
    const database = await openSoundDatabase();
    return new Promise(function (resolve, reject) {
      const transaction = database.transaction(SOUND_STORE, "readwrite");
      transaction.objectStore(SOUND_STORE).delete(SOUND_KEY);
      transaction.oncomplete = function () {
        database.close();
        resolve();
      };
      transaction.onerror = function () {
        database.close();
        reject(transaction.error || new Error("Could not remove custom sound."));
      };
      transaction.onabort = function () {
        database.close();
        reject(transaction.error || new Error("Could not remove custom sound."));
      };
    });
  }

  async function loadCustomOrderSound() {
    const sound = await getCustomOrderSound();
    if (customOrderSoundUrl) {
      URL.revokeObjectURL(customOrderSoundUrl);
      customOrderSoundUrl = null;
    }

    if (sound) {
      customOrderSoundUrl = URL.createObjectURL(sound);
      element("adminOrderSoundStatus").textContent =
        "Custom sound saved for this browser. It plays while this admin page is open and in the foreground.";
      element("removeAdminOrderSound").classList.remove("hidden");
    } else {
      element("adminOrderSoundStatus").textContent =
        "Default notification sound. Custom sound plays only while this admin page is open and in the foreground.";
      element("removeAdminOrderSound").classList.add("hidden");
    }
  }

  async function playOrderSound() {
    if (!customOrderSoundUrl) {
      return;
    }

    if (activeOrderAudio) {
      activeOrderAudio.pause();
      activeOrderAudio.currentTime = 0;
    }

    const audio = new Audio(customOrderSoundUrl);
    activeOrderAudio = audio;
    audio.addEventListener("ended", function () {
      if (activeOrderAudio === audio) {
        activeOrderAudio = null;
      }
    }, { once: true });
    try {
      await audio.play();
    } catch (error) {
      if (activeOrderAudio === audio) {
        activeOrderAudio = null;
      }
      console.warn("ADMIN ORDER SOUND PLAYBACK ERROR:", error.name);
      showMessage(
        "adminPushError",
        "Could not play the custom sound. Check its format and browser audio settings."
      );
    }
  }

  async function saveSelectedOrderSound() {
    hideMessage("adminPushError");
    const input = element("adminOrderSoundFile");
    const file = input.files[0];
    if (!file) {
      return;
    }
    if (!file.type.startsWith("audio/")) {
      input.value = "";
      showMessage("adminPushError", "Choose a valid audio file.");
      return;
    }
    if (file.size > MAX_SOUND_BYTES) {
      input.value = "";
      showMessage("adminPushError", "Audio files must be 2 MB or smaller.");
      return;
    }

    try {
      await saveCustomOrderSound(file);
      await loadCustomOrderSound();
      input.value = "";
      element("adminOrderSoundStatus").textContent =
        "Custom sound saved. Select Preview sound to enable and test playback.";
    } catch (error) {
      showMessage("adminPushError", error.message);
    }
  }

  async function removeOrderSound() {
    hideMessage("adminPushError");
    try {
      await deleteCustomOrderSound();
      await loadCustomOrderSound();
    } catch (error) {
      showMessage("adminPushError", error.message);
    }
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

        void playOrderSound();
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
    element("adminOrderSoundFile")
      .addEventListener("change", saveSelectedOrderSound);
    element("previewAdminOrderSound")
      .addEventListener("click", playOrderSound);
    element("removeAdminOrderSound")
      .addEventListener("click", removeOrderSound);
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

    try {
      await loadCustomOrderSound();
    } catch (error) {
      console.error("ADMIN ORDER SOUND STORAGE ERROR:", error.name);
      element("adminOrderSoundStatus").textContent =
        "Custom sound storage is unavailable in this browser; push alerts still work.";
    }
  }

  window.initializeAdminPush = initialize;
})();
