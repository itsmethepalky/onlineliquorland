import json
from html import escape as escape_html
import math
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from datetime import datetime, timezone
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import get_current_customer, get_current_user
from .config import settings
from .maintenance import get_maintenance_status
from .product_urls import product_slug
from .routers import admin, catalog, checkout, fonepay, whatsapp_webhook
from .supabase import supabase_admin
from .supabase_retry import execute_read_with_retry


BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = BASE_DIR / "frontend"
STATIC_ASSET_SUFFIXES = {
    ".css",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".png",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
}


def _bypasses_maintenance(path, method):
    if method == "OPTIONS":
        return True

    if path == "/admin" or path.startswith("/admin/"):
        return True

    if path in {"/login", "/forgot-password", "/reset-password"}:
        return True

    if Path(path).suffix.lower() in STATIC_ASSET_SUFFIXES:
        return True

    if path in {
        "/api/health",
        "/api/maintenance/status",
        "/api/auth/me",
        "/api/checkout/fonepay/success",
        "/api/checkout/fonepay/failure",
        "/api/payments/fonepay-callback",
        "/api/webhooks/whatsapp",
    }:
        return True

    return path == "/api/admin" or path.startswith("/api/admin/")


def _maintenance_page(message):
    safe_message = escape(message)
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        "<title>Store Maintenance | ONLINE LIQUOR LAND</title>"
        "<style>"
        "body{margin:0;min-height:100vh;display:grid;place-items:center;"
        "padding:24px;box-sizing:border-box;background:#f1e9dc;color:#211a1b;"
        "font:16px/1.6 system-ui,sans-serif;text-align:center}"
        "main{width:min(100%,520px);padding:36px 28px;background:#fbf8f1;"
        "border:1px solid #e5dbcc;border-radius:18px;box-shadow:"
        "0 18px 50px rgba(40,23,24,.14)}"
        "h1{font-family:Georgia,serif;color:#702d38}"
        "p{white-space:pre-wrap;color:#675c59}"
        "</style></head><body><main>"
        "<h1>Shop Is closed now</h1>"
        f"<p>{safe_message}</p>"
        "<strong>ONLINE LIQUOR LAND</strong>"
        "</main></body></html>"
    )


class CustomerPhoneUpdate(BaseModel):
    phone: str = Field(min_length=7, max_length=30)


app = FastAPI(
    title="Online Liquor Land API",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://onlineliquorland.com",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Cache-Control"],
)
app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
    compresslevel=5,
)


@app.middleware("http")
async def security_headers(request, call_next):
    path = request.url.path
    if _bypasses_maintenance(path, request.method):
        response = await call_next(request)
    else:
        try:
            maintenance = get_maintenance_status()
            if maintenance["active"]:
                message = (
                    maintenance["message"]
                    or "Our store is temporarily unavailable. Please check back soon."
                )
                if path.startswith("/api/"):
                    response = JSONResponse(
                        {
                            "detail": message,
                            "maintenance": True,
                        },
                        status_code=503,
                        headers={"Retry-After": "60"},
                    )
                else:
                    response = HTMLResponse(
                        _maintenance_page(message),
                        status_code=503,
                        headers={"Retry-After": "60"},
                    )
            else:
                response = await call_next(request)
        except HTTPException as exc:
            if path.startswith("/api/"):
                response = JSONResponse(
                    {"detail": exc.detail},
                    status_code=exc.status_code,
                    headers={"Retry-After": "60"},
                )
            else:
                response = HTMLResponse(
                    _maintenance_page(
                        "Store availability is temporarily unknown. Please try again soon."
                    ),
                    status_code=exc.status_code,
                    headers={"Retry-After": "60"},
                )

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(self), "
        "payment=(), usb=()"
    )

    if response.status_code < 400 and request.url.path in {
        "/api/categories",
        "/api/brands",
        "/api/products",
    } and request.method == "GET":
        response.headers["Cache-Control"] = (
            "public, max-age=5, stale-while-revalidate=15"
        )
    elif request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif Path(request.url.path).suffix.lower() in {
        ".css",
        ".ico",
        ".js",
        ".svg",
        ".woff",
        ".woff2",
    }:
        response.headers["Cache-Control"] = (
            "public, max-age=300, stale-while-revalidate=3600"
        )

    return response


app.include_router(catalog.router)
app.include_router(checkout.router)
app.include_router(fonepay.router)
app.include_router(admin.router)
app.include_router(whatsapp_webhook.router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "Online Liquor Land API",
    }


@app.get("/api/maintenance/status")
def public_maintenance_status():
    return get_maintenance_status()


@app.get("/api/public/contact")
def public_contact():
    return {
        "business_name": "ONLINE LIQUOR LAND",
        "email": settings.STORE_PUBLIC_EMAIL.strip(),
        "phone": settings.STORE_CONTACT_PHONE.strip(),
    }


def _firebase_messaging_public_config():
    if not settings.firebase_web_configured():
        return {
            "configured": False,
            "firebaseConfig": None,
        }

    return {
        "configured": True,
        "firebaseConfig": settings.firebase_web_config(),
        "vapidKey": settings.FIREBASE_WEB_VAPID_KEY,
    }


@app.get("/api/firebase-messaging-config")
def firebase_messaging_public_config():
    return _firebase_messaging_public_config()


@app.get("/api/firebase-messaging-config.js")
def firebase_messaging_service_worker_config():
    config = _firebase_messaging_public_config()
    script = (
        "self.firebaseMessagingPublicConfig = "
        + json.dumps(config, separators=(",", ":"))
        + ";"
    )
    return Response(
        content=script,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /product\n"
        "Disallow: /api/\n"
        f"Sitemap: {settings.SEO_SITE_URL.rstrip('/')}/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
def sitemap_xml():
    paths = (
        "/",
        "/shop",
        "/about",
        "/contact",
        "/privacy-policy",
        "/terms-and-conditions",
    )
    site_url = settings.SEO_SITE_URL.rstrip("/")
    urls = "".join(
        f"<url><loc>{escape(site_url + path)}</loc></url>"
        for path in paths
    )

    product_offset = 0
    product_page_size = 1000
    while True:
        response = execute_read_with_retry(
            supabase_admin
            .table("products")
            .select("id,name")
            .eq("is_active", True)
            .order("id")
            .range(product_offset, product_offset + product_page_size - 1),
            operation="SEO SITEMAP PRODUCTS",
        )
        products = response.data or []
        urls += "".join(
            "<url><loc>"
            + escape(
                f"{site_url}/product/{product_slug(product['name'])}"
            )
            + "</loc></url>"
            for product in products
        )
        if len(products) < product_page_size:
            break
        product_offset += product_page_size

    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>"
        ),
        media_type="application/xml",
    )


@app.get("/api/auth/me")
def auth_me(
    user=Depends(get_current_user),
):
    try:
        response = execute_read_with_retry(
            supabase_admin
            .table("profiles")
            .select(
                "id,full_name,email,phone,role,is_active"
            )
            .eq("id", str(user.id))
            .limit(1),
            operation="AUTH PROFILE",
        )
    except HTTPException:
        raise
    except Exception as exc:
        print(
            "AUTH PROFILE ERROR:",
            repr(exc),
            flush=True,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to load account profile",
        )

    rows = response.data or []

    if not rows:
        raise HTTPException(
            status_code=403,
            detail="Profile not found",
        )

    profile = rows[0]

    if not profile.get("is_active", False):
        raise HTTPException(
            status_code=403,
            detail="Account is inactive",
        )

    return {
        "id": str(user.id),
        "email": (
            profile.get("email")
            or getattr(user, "email", None)
        ),
        "role": profile.get("role"),
        "profile": profile,
    }



@app.get("/api/account/me")
def account_me(
    current=Depends(get_current_customer),
):
    return {
        "profile": current["profile"],
        "customer": current["customer"],
    }


@app.put("/api/account/phone")
def update_account_phone(
    payload: CustomerPhoneUpdate,
    current=Depends(get_current_customer),
):
    phone = payload.phone.strip()
    if not 7 <= len(phone) <= 30:
        raise HTTPException(
            status_code=422,
            detail="Phone number must be 7 to 30 characters.",
        )

    try:
        supabase_admin.rpc(
            "update_store_customer_phone",
            {
                "p_user_id": current["profile"]["id"],
                "p_phone": phone,
            },
        ).execute()
    except Exception as exc:
        error_text = str(exc).lower()
        print(
            "ACCOUNT PHONE UPDATE ERROR:",
            repr(exc),
            flush=True,
        )
        if (
            "pgrst202" in error_text
            and "update_store_customer_phone" in error_text
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "Phone updates are not installed yet. "
                    "Apply database migration 016."
                ),
            ) from exc
        raise HTTPException(
            status_code=500,
            detail="Unable to update your phone number.",
        ) from exc

    return {"phone": phone}


@app.get("/api/account/orders")
def account_orders(
    current=Depends(get_current_customer),
):
    customer_id = current["customer"]["id"]

    try:
        response = (
            supabase_admin
            .table("orders")
            .select(
                """
                id,
                order_no,
                status,
                subtotal,
                discount,
                tax,
                total_amount,
                amount_paid,
                payment_method,
                payment_status,
                shipping_address,
                phone,
                notes,
                created_at,
                updated_at,
                order_items(
                    id,
                    product_id,
                    quantity,
                    unit_price,
                    discount,
                    subtotal,
                    products(
                        id,
                        name,
                        sku
                    )
                )
                """
            )
            .eq("customer_id", customer_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:
        print(
            "ACCOUNT ORDERS ERROR:",
            repr(exc),
            flush=True,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to load orders",
        )

    return response.data or []


@app.get("/api/account/orders/{order_id}")
def account_order(
    order_id: int,
    current=Depends(get_current_customer),
):
    customer_id = current["customer"]["id"]

    try:
        response = (
            supabase_admin
            .table("orders")
            .select(
                """
                id,
                order_no,
                status,
                subtotal,
                discount,
                tax,
                total_amount,
                payment_method,
                payment_status,
                shipping_address,
                phone,
                notes,
                created_at,
                updated_at,
                order_items(
                    id,
                    product_id,
                    quantity,
                    unit_price,
                    discount,
                    subtotal,
                    products(
                        id,
                        name,
                        sku
                    )
                )
                """
            )
            .eq("id", order_id)
            .eq("customer_id", customer_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        print(
            "ACCOUNT ORDER ERROR:",
            repr(exc),
            flush=True,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to load order",
        )

    if not response.data:
        raise HTTPException(
            status_code=404,
            detail="Order not found",
        )

    return response.data


def serve_page(filename: str):
    return FileResponse(FRONTEND_DIR / filename)


def render_product_page(product: dict | None = None) -> HTMLResponse:
    site_url = settings.SEO_SITE_URL.rstrip("/")
    is_product_page = product is not None
    product_name = str(product.get("name") or "Product") if product else "Product"
    canonical_url = (
        f"{site_url}/product/{product_slug(product_name)}"
        if product
        else f"{site_url}/product"
    )
    description = (
        " ".join(str(product.get("description") or "").split())
        if product
        else ""
    ) or (
        f"Shop {product_name} online at ONLINE LIQUOR LAND. "
        "View current product details and availability."
        if product
        else "Explore product details and availability at ONLINE LIQUOR LAND. "
        "Browse wines, spirits, and favorites online."
    )
    if len(description) > 155:
        shortened_description = description[:152].rsplit(" ", 1)[0].rstrip()
        meta_description = (shortened_description or description[:152].rstrip()) + "..."
    else:
        meta_description = description
    image_urls = []
    if product:
        for image in product.get("images", []):
            image_url = image.get("image_url")
            if not image_url:
                continue
            absolute_url = urljoin(site_url + "/", str(image_url))
            if urlparse(absolute_url).scheme in {"http", "https"}:
                image_urls.append(absolute_url)

    price = None
    if product and product.get("unit_price") is not None:
        try:
            numeric_price = float(product["unit_price"])
            if math.isfinite(numeric_price) and numeric_price >= 0:
                price = f"{numeric_price:.2f}"
            else:
                print(
                    "PRODUCT SEO: omitted offer because unit price is invalid",
                    flush=True,
                )
        except (TypeError, ValueError):
            print(
                "PRODUCT SEO: omitted offer because unit price is invalid",
                flush=True,
            )

    product_data = None
    breadcrumb_data = None
    if product:
        product_data = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": product_name,
            "description": description,
            "url": canonical_url,
            "mainEntityOfPage": canonical_url,
        }
        if image_urls:
            product_data["image"] = image_urls
        if product.get("brand_name"):
            product_data["brand"] = {
                "@type": "Brand",
                "name": str(product["brand_name"]),
            }
        if product.get("category_name"):
            product_data["category"] = str(product["category_name"])
        if price is not None:
            product_data["offers"] = {
                "@type": "Offer",
                "priceCurrency": "NPR",
                "price": price,
                "availability": (
                    "https://schema.org/InStock"
                    if int(product.get("stock_quantity") or 0) > 0
                    else "https://schema.org/OutOfStock"
                ),
                "itemCondition": "https://schema.org/NewCondition",
                "url": canonical_url,
                "seller": {
                    "@type": "Organization",
                    "name": "ONLINE LIQUOR LAND",
                },
            }
        breadcrumb_data = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": 1,
                    "name": "Home",
                    "item": f"{site_url}/",
                },
                {
                    "@type": "ListItem",
                    "position": 2,
                    "name": "Shop",
                    "item": f"{site_url}/shop",
                },
                {
                    "@type": "ListItem",
                    "position": 3,
                    "name": product_name,
                    "item": canonical_url,
                },
            ],
        }

    structured_data = ""
    if product_data is not None:
        structured_data += (
            '<script id="productStructuredData" type="application/ld+json">'
            + json.dumps(
                product_data,
                ensure_ascii=False,
                separators=(",", ":"),
            ).replace("<", "\\u003c")
            + "</script>"
        )
    if breadcrumb_data is not None:
        structured_data += (
            '<script type="application/ld+json">'
            + json.dumps(
                breadcrumb_data,
                ensure_ascii=False,
                separators=(",", ":"),
            ).replace("<", "\\u003c")
            + "</script>"
        )
    fallback_image = f"{site_url}/favicon-48x48.png"
    page_title = (
        f"{product_name} — ONLINE LIQUOR LAND"
        if product
        else "Product — ONLINE LIQUOR LAND"
    )
    meta_title = (
        f"{product_name} | ONLINE LIQUOR LAND"
        if product
        else "Product | ONLINE LIQUOR LAND"
    )
    image_alt = (
        f"{product_name} product image"
        if product and image_urls
        else "ONLINE LIQUOR LAND"
    )
    safe_values = {
        "__PRODUCT_META_DESCRIPTION__": escape_html(meta_description, quote=True),
        "__PRODUCT_CANONICAL_URL__": escape_html(canonical_url, quote=True),
        "__PRODUCT_ROBOTS__": "index,follow" if is_product_page else "noindex,follow",
        "__PRODUCT_META_TITLE__": escape_html(meta_title, quote=True),
        "__PRODUCT_TITLE__": escape_html(page_title, quote=True),
        "__PRODUCT_IMAGE_URL__": escape_html(
            image_urls[0] if image_urls else fallback_image,
            quote=True,
        ),
        "__PRODUCT_IMAGE_ALT__": escape_html(image_alt, quote=True),
        "__PRODUCT_PRICE__": price or "",
    }
    html = (FRONTEND_DIR / "product.html").read_text(encoding="utf-8")
    for placeholder, value in safe_values.items():
        html = html.replace(placeholder, value)
    if structured_data:
        html = html.replace("</head>", structured_data + "</head>", 1)
    return HTMLResponse(content=html)


@app.get("/", include_in_schema=False)
def frontend_home():
    return serve_page("index.html")


@app.get("/admin/orders", include_in_schema=False)
def admin_orders_page():
    return FileResponse(FRONTEND_DIR / "admin-orders.html")


@app.get("/admin/order", include_in_schema=False)
def admin_order_page():
    return FileResponse(FRONTEND_DIR / "admin-order.html")


@app.get("/admin/products", include_in_schema=False)
def admin_products_page():
    return FileResponse(FRONTEND_DIR / "admin-products.html")


@app.get("/admin/account", include_in_schema=False)
def admin_account_page():
    return FileResponse(FRONTEND_DIR / "admin-account.html")


@app.get("/admin/customers", include_in_schema=False)
def admin_customers_page():
    return FileResponse(FRONTEND_DIR / "admin-customers.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(FRONTEND_DIR / "admin.html")


@app.get("/shop", include_in_schema=False)
def frontend_shop():
    return serve_page("shop.html")


@app.get("/about", include_in_schema=False)
def frontend_about():
    return serve_page("about.html")


@app.get("/contact", include_in_schema=False)
def frontend_contact():
    return serve_page("contact.html")


@app.get("/privacy-policy", include_in_schema=False)
def frontend_privacy_policy():
    return serve_page("privacy-policy.html")


@app.get("/terms-and-conditions", include_in_schema=False)
def frontend_terms_and_conditions():
    return serve_page("terms-and-conditions.html")


@app.get("/product", include_in_schema=False)
def frontend_product(request: Request):
    product_id = request.query_params.get("id")
    if product_id is None:
        return RedirectResponse(url="/shop", status_code=301)

    try:
        parsed_product_id = int(product_id)
    except ValueError:
        return RedirectResponse(url="/shop", status_code=301)
    if parsed_product_id <= 0:
        return RedirectResponse(url="/shop", status_code=301)

    product = catalog.get_product(parsed_product_id)
    return RedirectResponse(
        url=f"/product/{product_slug(product['name'])}",
        status_code=301,
    )


@app.get("/product/", include_in_schema=False)
def frontend_product_index_slash():
    return RedirectResponse(url="/shop", status_code=301)


@app.get("/product/{slug}", include_in_schema=False)
def frontend_product_by_slug(slug: str):
    product = catalog.get_product_by_slug(slug)
    return render_product_page(product)


@app.get("/cart", include_in_schema=False)
def frontend_cart():
    return serve_page("cart.html")


@app.get("/wishlist", include_in_schema=False)
def frontend_wishlist():
    return serve_page("wishlist.html")


@app.get("/checkout", include_in_schema=False)
def frontend_checkout():
    return serve_page("checkout.html")


@app.get("/login", include_in_schema=False)
def frontend_login():
    return serve_page("login.html")


@app.get("/forgot-password", include_in_schema=False)
def frontend_forgot_password():
    return serve_page("forgot-password.html")


@app.get("/reset-password", include_in_schema=False)
def frontend_reset_password():
    return serve_page("reset-password.html")


@app.get("/register", include_in_schema=False)
def frontend_register():
    return serve_page("register.html")


@app.get("/account", include_in_schema=False)
def frontend_account():
    return serve_page("account.html")


@app.get("/orders", include_in_schema=False)
def frontend_orders():
    return serve_page("orders.html")


@app.get("/order", include_in_schema=False)
def frontend_order():
    return serve_page("order.html")
@app.get("/api/promos")
def get_active_promotions():
    now = datetime.now(timezone.utc).isoformat()

    try:
        response = (
            supabase_admin
            .table("store_promotions")
            .select(
                "id,code,name,description,discount_type,"
                "discount_value,minimum_order_amount,"
                "maximum_discount_amount,starts_at,expires_at,"
                "usage_limit,usage_count,is_active"
            )
            .eq("is_active", True)
            .lte("starts_at", now)
            .order("created_at", desc=True)
            .execute()
        )

        promotions = response.data or []

        # Exclude expired promotions and promotions whose usage
        # limits have been reached.
        active_promotions = []

        for promo in promotions:
            expires_at = promo.get("expires_at")
            usage_limit = promo.get("usage_limit")
            usage_count = promo.get("usage_count", 0)

            if expires_at:
                expiry = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                )

                if expiry <= datetime.now(timezone.utc):
                    continue

            if (
                usage_limit is not None
                and usage_count >= usage_limit
            ):
                continue

            active_promotions.append(promo)

        return active_promotions

    except Exception as exc:
        print("ACTIVE PROMOTIONS ERROR:", repr(exc), flush=True)
        raise HTTPException(
            status_code=500,
            detail="Unable to load promotions",
        )


app.mount(
    "/",
    StaticFiles(
        directory=FRONTEND_DIR,
        html=True,
    ),
    name="frontend",
)
