(function () {
  function getItems() {
    const items = JSON.parse(localStorage.getItem("blueMoonCart") || "[]");
    if (!Array.isArray(items)) {
      throw new Error("The saved cart has an invalid format.");
    }
    return items;
  }

  function add(product, stockQuantity) {
    const productId = Number(product?.id || product?.product_id);
    const available = Number(stockQuantity);
    if (!Number.isInteger(productId) || productId <= 0 ||
        !Number.isInteger(available) || available <= 0) {
      throw new Error("This product is no longer available.");
    }

    const items = getItems();
    const existing = items.find(
      item => Number(item.product_id) === productId
    );
    const currentQuantity = Number(existing?.quantity || 0);
    if (currentQuantity >= available) {
      throw new Error("Your cart already contains the available stock.");
    }

    if (existing) {
      existing.quantity = currentQuantity + 1;
    } else {
      const images = product.images || [];
      const primaryImage = images.find(image => image.is_primary) || images[0];
      items.push({
        product_id: productId,
        name: product.name || "Product",
        sku: product.sku || "",
        unit_price: Number(product.unit_price || 0),
        image_url: product.image_url || primaryImage?.image_url || null,
        quantity: 1
      });
    }

    localStorage.setItem("blueMoonCart", JSON.stringify(items));
    window.dispatchEvent(new CustomEvent("store:cart-updated"));
    return items.reduce(
      (count, item) => count + Number(item.quantity || 0),
      0
    );
  }

  window.storefrontCart = { add };
})();
