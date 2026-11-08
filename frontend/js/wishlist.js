(function () {
  const STORAGE_KEY = "blueMoonWishlist";

  function getItems() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(saved)
        ? saved.filter(item => Number.isInteger(Number(item.product_id)))
        : [];
    } catch (error) {
      console.error("Unable to read the wishlist:", error);
      return [];
    }
  }

  function saveItems(items) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  }

  function add(product) {
    const productId = Number(product?.id || product?.product_id);
    if (!Number.isInteger(productId) || productId <= 0) {
      throw new Error("A valid product is required to save to the wishlist.");
    }

    const items = getItems();
    if (items.some(item => Number(item.product_id) === productId)) {
      return false;
    }
    if (items.length >= 100) {
      throw new Error("Your wishlist can contain up to 100 products.");
    }

    const images = product.images || [];
    const primaryImage = images.find(image => image.is_primary) || images[0];
    items.push({
      product_id: productId,
      name: product.name || "Product",
      sku: product.sku || "",
      unit_price: Number(product.unit_price || 0),
      image_url: product.image_url || primaryImage?.image_url || null
    });
    saveItems(items);
    return true;
  }

  function remove(productId) {
    const id = Number(productId);
    saveItems(
      getItems().filter(item => Number(item.product_id) !== id)
    );
  }

  window.productWishlist = { getItems, add, remove };
})();
