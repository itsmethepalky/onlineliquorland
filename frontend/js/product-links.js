function productSlug(name) {
  const slug = String(name || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  return slug || "product";
}

function productUrl(product) {
  return `/product/${encodeURIComponent(productSlug(product.name))}`;
}
