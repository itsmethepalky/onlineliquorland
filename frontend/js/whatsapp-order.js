(function () {
  const links = document.querySelectorAll(".whatsapp-order-link");
  if (!links.length) return;

  function getCart() {
    try {
      const cart = JSON.parse(
        localStorage.getItem("blueMoonCart") || "[]"
      );
      return Array.isArray(cart) ? cart : [];
    } catch (error) {
      return [];
    }
  }

  function getOrderMessage() {
    const cart = getCart();
    if (!cart.length) {
      return "Hi, I would like to place an order. Please help me.";
    }

    const lines = ["Hi, I would like to order:"];
    let total = 0;

    cart.forEach(item => {
      const quantity = Number(item.quantity);
      const price = Number(item.unit_price);
      if (
        !Number.isFinite(quantity) ||
        quantity <= 0 ||
        !Number.isFinite(price) ||
        price < 0
      ) {
        return;
      }

      const itemTotal = quantity * price;
      total += itemTotal;
      lines.push(
        `- ${String(item.name || "Product")} x ${quantity} — NPR ${itemTotal.toFixed(2)}`
      );
    });

    if (lines.length === 1) {
      return "Hi, I would like to place an order. Please help me.";
    }

    lines.push(
      `Items subtotal (delivery quoted separately): NPR ${total.toFixed(2)}`
    );
    return lines.join("\n").slice(0, 2500);
  }

  function updateLinks() {
    links.forEach(link => {
      const phone = String(link.dataset.phone || "").replace(/\D/g, "");
      if (!phone) return;

      link.href =
        `https://wa.me/${phone}?text=${encodeURIComponent(getOrderMessage())}`;
    });
  }

  links.forEach(link => {
    link.addEventListener("click", updateLinks);
  });
  window.addEventListener("storage", updateLinks);
  updateLinks();
})();
