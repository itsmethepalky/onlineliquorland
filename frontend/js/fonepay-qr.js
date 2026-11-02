(() => {
  const modal = document.createElement("section");
  modal.className = "fonepay-qr-modal hidden";
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "fonepayQrTitle");
  modal.innerHTML = `
    <div class="fonepay-qr-card">
      <button type="button" class="fonepay-qr-close" aria-label="Close payment dialog">&times;</button>
      <p class="eyebrow">SECURE PAYMENT</p>
      <h2 id="fonepayQrTitle">Scan with a banking app</h2>
      <p class="fonepay-qr-order"></p>
      <p class="fonepay-qr-amount"></p>
      <img class="fonepay-qr-image hidden" alt="Fonepay payment QR code">
      <p class="fonepay-qr-message" aria-live="polite"></p>
      <div class="fonepay-qr-actions">
        <button type="button" class="store-button primary fonepay-qr-check">Check payment</button>
        <button type="button" class="store-button secondary fonepay-qr-retry hidden">Refresh QR</button>
      </div>
      <a class="fonepay-qr-orders" href="/orders">View my orders</a>
    </div>
  `;
  document.body.append(modal);

  const orderLabel = modal.querySelector(".fonepay-qr-order");
  const amountLabel = modal.querySelector(".fonepay-qr-amount");
  const image = modal.querySelector(".fonepay-qr-image");
  const message = modal.querySelector(".fonepay-qr-message");
  const checkButton = modal.querySelector(".fonepay-qr-check");
  const retryButton = modal.querySelector(".fonepay-qr-retry");
  let activePrn = "";
  let activeOrderId = 0;
  let activeAmount = null;
  let clearCartOnPaid = false;
  let onPaid = null;
  let paymentFinished = false;
  let pollTimer = null;
  let requestPending = false;

  function getAuthToken() {
    return window.supabaseClient.auth.getSession().then(({ data, error }) => {
      if (error) {
        throw error;
      }
      const token = data?.session?.access_token;
      if (!token) {
        window.location.href =
          `/login?redirect=${encodeURIComponent(window.location.pathname)}`;
        throw new Error("Please sign in to continue.");
      }
      return token;
    });
  }

  async function postJson(url, payload) {
    const token = await getAuthToken();
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Unable to process the Fonepay request.");
    }
    return data;
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function closeModal() {
    stopPolling();
    modal.classList.add("hidden");
  }

  function handlePaid(result) {
    stopPolling();
    paymentFinished = true;
    message.textContent =
      `Payment confirmed for ${result.order_no}. ` +
      `Remaining balance: NPR ${Number(result.remaining_balance).toFixed(2)}.`;
    checkButton.disabled = true;
    retryButton.classList.add("hidden");
    if (clearCartOnPaid) {
      try {
        localStorage.removeItem("blueMoonCart");
        sessionStorage.removeItem("blueMoonDeliveryLocation");
      } catch (error) {
        console.warn("Completed checkout could not be cleared from browser storage.");
      }
    }
    if (typeof onPaid === "function") {
      onPaid(result);
    }
  }

  async function checkPayment() {
    if (!activePrn || requestPending || paymentFinished) {
      return;
    }
    requestPending = true;
    checkButton.disabled = true;
    try {
      const result = await postJson("/api/payments/fonepay-verify", {
        prn: activePrn,
      });
      if (result.status === "paid") {
        handlePaid(result);
      } else if (result.status === "failed") {
        stopPolling();
        message.textContent =
          "Fonepay did not complete this payment. You can request a new QR.";
        retryButton.classList.remove("hidden");
      } else {
        message.textContent =
          "Payment is not confirmed yet. This page will check again.";
      }
    } catch (error) {
      message.textContent = error.message || "Unable to check payment status.";
    } finally {
      requestPending = false;
      checkButton.disabled = paymentFinished;
    }
  }

  async function loadQr() {
    if (requestPending || !activeOrderId) {
      return;
    }
    requestPending = true;
    retryButton.disabled = true;
    checkButton.disabled = true;
    message.textContent = "Preparing your secure Fonepay QR...";
    try {
      const payload = { order_id: activeOrderId };
      if (activeAmount !== null) {
        payload.amount = activeAmount;
      }
      const result = await postJson("/api/checkout/fonepay-qr", payload);
      activePrn = result.prn;
      orderLabel.textContent = `Order ${result.order_no}`;
      amountLabel.textContent = `Scan to pay NPR ${Number(result.amount).toFixed(2)}`;
      image.src = result.qr_image;
      image.classList.remove("hidden");
      message.textContent =
        "Open your banking or wallet app, scan the QR, and complete payment.";
      retryButton.classList.add("hidden");
      stopPolling();
      pollTimer = window.setInterval(checkPayment, 5000);
    } catch (error) {
      image.removeAttribute("src");
      image.classList.add("hidden");
      message.textContent = error.message || "Unable to generate a payment QR.";
      retryButton.classList.remove("hidden");
    } finally {
      requestPending = false;
      retryButton.disabled = false;
      checkButton.disabled = false;
    }
  }

  modal.querySelector(".fonepay-qr-close").addEventListener("click", closeModal);
  checkButton.addEventListener("click", checkPayment);
  retryButton.addEventListener("click", loadQr);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) {
      closeModal();
    }
  });

  window.FonepayQr = {
    open(orderId, amount, options = {}) {
      activeOrderId = Number(orderId);
      activeAmount = amount === null || amount === undefined
        ? null
        : String(amount);
      clearCartOnPaid = options.clearCart === true;
      onPaid = typeof options.onPaid === "function"
        ? options.onPaid
        : null;
      paymentFinished = false;
      activePrn = "";
      checkButton.disabled = false;
      retryButton.classList.add("hidden");
      image.classList.add("hidden");
      modal.classList.remove("hidden");
      modal.querySelector(".fonepay-qr-close").focus();
      return loadQr();
    },
  };
})();
