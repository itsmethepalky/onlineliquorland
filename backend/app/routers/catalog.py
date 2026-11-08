from typing import Optional
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, HTTPException, Query

from ..supabase import supabase_admin
from ..product_urls import product_slug

router = APIRouter(prefix="/api", tags=["Catalog"])


# Small, controlled retry for temporary Supabase/PostgREST
# connection problems. Do NOT retry normal database/API errors.
MAX_RETRIES = 3
RETRY_DELAYS = (0.2, 0.5, 1.0)


def is_transient_error(exc: Exception) -> bool:
    error_name = type(exc).__name__
    error_text = repr(exc)

    transient_names = {
        "RemoteProtocolError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutException",
        "ConnectionTerminated",
    }

    if isinstance(exc, httpx.RequestError):
        return True

    if error_name in transient_names:
        return True

    transient_text = (
        "RemoteProtocolError",
        "ConnectionTerminated",
        "ConnectionResetError",
        "Connection aborted",
        "connection reset",
        "connection closed",
        "server disconnected",
    )

    return any(
        text.lower() in error_text.lower()
        for text in transient_text
    )


def execute_with_retry(query):
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            return query.execute()

        except Exception as exc:
            last_error = exc

            if not is_transient_error(exc):
                raise

            print(
                f"SUPABASE TRANSIENT ERROR "
                f"(attempt {attempt + 1}/{MAX_RETRIES}): "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])

    print(
        "SUPABASE SERVICE TEMPORARILY UNAVAILABLE:",
        type(last_error).__name__ if last_error else "UnknownError",
        repr(last_error),
        flush=True,
    )

    raise HTTPException(
        status_code=503,
        detail="Store data service temporarily unavailable. Please try again.",
        headers={"Retry-After": "2"},
    )


def clean_product(product: dict) -> dict:
    images = product.get("product_images") or []

    images.sort(
        key=lambda image: (
            not image.get("is_primary", False),
            image.get("sort_order", 0),
            image.get("id", 0),
        )
    )

    return {
        "id": product.get("id"),
        "sku": product.get("sku"),
        "name": product.get("name"),
        "description": product.get("description"),
        "category_id": product.get("category_id"),
        "category_name": product.get("category_name"),
        "brand_id": product.get("brand_id"),
        "brand_name": product.get("brand_name"),
        "unit_price": product.get("unit_price"),
        "stock_quantity": product.get("stock_quantity"),
        "is_active": product.get("is_active"),
        "images": [
            {
                "id": image.get("id"),
                "image_url": image.get("image_url"),
                "is_primary": image.get("is_primary", False),
                "sort_order": image.get("sort_order", 0),
            }
            for image in images
        ],
    }


def add_taxonomy_names(products: list[dict]) -> None:
    category_ids = {
        product["category_id"]
        for product in products
        if product.get("category_id") is not None
    }
    brand_ids = {
        product["brand_id"]
        for product in products
        if product.get("brand_id") is not None
    }

    categories = {}
    if category_ids:
        response = execute_with_retry(
            supabase_admin
            .table("categories")
            .select("id,name")
            .in_("id", list(category_ids))
        )
        categories = {
            category["id"]: category["name"]
            for category in (response.data or [])
        }

    brands = {}
    if brand_ids:
        response = execute_with_retry(
            supabase_admin
            .table("brands")
            .select("id,name")
            .in_("id", list(brand_ids))
        )
        brands = {
            brand["id"]: brand["name"]
            for brand in (response.data or [])
        }

    for product in products:
        product["category_name"] = categories.get(
            product.get("category_id")
        )
        product["brand_name"] = brands.get(
            product.get("brand_id")
        )


def add_product_images(products: list[dict]) -> None:
    product_ids = [
        product["id"]
        for product in products
        if product.get("id") is not None
    ]
    if not product_ids:
        return

    response = execute_with_retry(
        supabase_admin
        .table("product_images")
        .select("id,product_id,image_url,is_primary,sort_order")
        .in_("product_id", product_ids)
        .order("sort_order")
    )

    images_by_product: dict[int, list[dict]] = {}
    for image in response.data or []:
        images_by_product.setdefault(image["product_id"], []).append(image)

    for product in products:
        product["product_images"] = images_by_product.get(
            product["id"],
            [],
        )


def add_product_metadata(products: list[dict]) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        taxonomy_future = executor.submit(
            add_taxonomy_names,
            products,
        )
        images_future = executor.submit(
            add_product_images,
            products,
        )
        taxonomy_future.result()
        images_future.result()


@router.get("/categories")
def list_categories():
    query = (
        supabase_admin
        .table("categories")
        .select("id,name,description")
        .eq("is_active", True)
        .order("name")
    )

    response = execute_with_retry(query)

    return response.data or []


@router.get("/brands")
def list_brands():
    query = (
        supabase_admin
        .table("brands")
        .select("id,name")
        .eq("is_active", True)
        .order("name")
    )

    response = execute_with_retry(query)

    return response.data or []


@router.get("/products")
def list_products(
    search: Optional[str] = Query(default=None, max_length=100),
    category_id: Optional[int] = None,
    brand_id: Optional[int] = None,
    in_stock: bool = False,
    limit: Optional[int] = Query(default=None, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    sort: str = Query(default="name", pattern="^(name|price-low|price-high)$"),
):
    query = (
        supabase_admin
        .table("products")
        .select(
            """
            id,
            sku,
            name,
            description,
            category_id,
            brand_id,
            unit_price,
            stock_quantity,
            is_active
            """
        )
        .eq("is_active", True)
    )

    if search and search.strip():
        text = search.strip()
        escaped_text = (
            text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        query = query.or_(
            f'name.ilike."%{escaped_text}%",'
            f'sku.ilike."%{escaped_text}%"'
        )

    if category_id is not None:
        query = query.eq("category_id", category_id)

    if brand_id is not None:
        query = query.eq("brand_id", brand_id)

    if in_stock:
        query = query.gt("stock_quantity", 0)

    if sort == "price-low":
        query = query.order("unit_price").order("name")
    elif sort == "price-high":
        query = query.order("unit_price", desc=True).order("name")
    else:
        query = query.order("name")

    if limit is not None:
        query = query.range(offset, offset + limit - 1)

    response = execute_with_retry(query)
    products = response.data or []
    add_product_metadata(products)

    return [
        clean_product(product)
        for product in products
    ]


@router.get("/products/stock")
def get_products_stock(
    product_ids: list[int] = Query(...),
):
    if (
        not product_ids
        or len(product_ids) > 100
        or any(product_id <= 0 for product_id in product_ids)
    ):
        raise HTTPException(
            status_code=422,
            detail="Provide between 1 and 100 valid product IDs.",
        )

    response = execute_with_retry(
        supabase_admin
        .table("products")
        .select("id,stock_quantity,is_active,updated_at")
        .in_("id", list(set(product_ids)))
    )
    return response.data or []


@router.get("/products/{product_id}")
def get_product(product_id: int):
    query = (
        supabase_admin
        .table("products")
        .select(
            """
            id,
            sku,
            name,
            description,
            category_id,
            brand_id,
            unit_price,
            stock_quantity,
            is_active
            """
        )
        .eq("id", product_id)
        .eq("is_active", True)
        .maybe_single()
    )

    response = execute_with_retry(query)

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    product = response.data
    add_product_metadata([product])
    return clean_product(product)


@router.get("/products/by-slug/{slug}")
def get_product_by_slug(slug: str):
    page_size = 1000
    offset = 0
    matches = []

    while True:
        response = execute_with_retry(
            supabase_admin
            .table("products")
            .select("id,name")
            .eq("is_active", True)
            .order("id")
            .range(offset, offset + page_size - 1)
        )
        products = response.data or []
        matches.extend(
            product
            for product in products
            if product_slug(product["name"]) == slug
        )
        if len(products) < page_size:
            break
        offset += page_size

    if not matches:
        raise HTTPException(
            status_code=404,
            detail="Product not found",
        )

    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="Product URL is ambiguous",
        )

    return get_product(matches[0]["id"])
