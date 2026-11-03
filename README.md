# ONLINE LIQUOR LAND

Independent ecommerce website for ONLINE LIQUOR LAND.

This application uses the existing Supabase project for:

- PostgreSQL database
- Supabase Auth
- Supabase Storage

The POS application and this ecommerce application are separate applications
with separate frontends and backends while sharing the same Supabase data.

## Render availability check

The GitHub Actions workflow at `.github/workflows/keep-render-awake.yml`
requests the deployed `/api/health` endpoint every 10 minutes and retries
transient failures, including a cold start. This can reduce idle spin-down on
Render's free instance, but GitHub Actions schedules can be delayed or paused
and cannot guarantee that a free instance never sleeps. For guaranteed
always-on service, use a Render plan with always-on instances.

## Local store development

Run the store backend from the `backend` directory with
`python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`, then
open `http://127.0.0.1:8000/checkout`. The storefront uses same-origin API
requests; do not serve it from a separate static server or the POS API port
8001. Local Fonepay checkout uses the UAT base URL by default but still
requires Fonepay UAT merchant code, shared secret, API username, and API
password in `backend/.env`. Without all four values, checkout intentionally
stops before creating an order; it does not simulate or mark a payment as paid.

## Route-based checkout delivery

Checkout uses Leaflet with OpenStreetMap tiles for map pin selection. Address
search and reverse geocoding use Nominatim, and the backend calculates walking
distance through OSRM, choosing the shortest route returned when alternatives
are available. The landing page silently requests browser location access and
saves a successful result for the current browser tab. Checkout sends the
coordinates to the backend, which calculates route distance and delivery fee.
The customer's browser controls permission; checkout requests location if no
recent landing-page location is available. Customers can instead search for
an address or adjust the pin using the "Set location on map" control. OSRM uses
different road data and routing rules from Google Maps, so route distances may
still differ.
The checkout price is the admin-set base fare plus NPR 50 per route kilometer.
Free-delivery promotions continue to waive the delivery fee. Orders cannot be
placed until route pricing succeeds.

Configure these backend environment variables:

- `OSM_NOMINATIM_URL`: Nominatim service base URL; defaults to
  `https://nominatim.openstreetmap.org`.
- `OSM_ROUTING_URL`: OSRM foot-profile service base URL; defaults to
  `https://routing.openstreetmap.de/routed-foot`.
- `OSM_ROUTING_FALLBACK_URL`: optional OSRM-compatible foot-profile service,
  tried when the primary cannot be reached or returns an error.
- `STORE_CONTACT_EMAIL`: contact included with Nominatim requests.
- `STORE_ORIGIN_LATITUDE` and `STORE_ORIGIN_LONGITUDE`: private store coordinates.
  Do not put the origin in frontend code or expose it through an API response.

## Database migration order

For a **new database**, apply the numbered migrations in order through 011,
then apply these checkout migrations in this order:

1. `database/migrations/012_server_side_delivery_promotions.sql`
2. `database/migrations/013_create_store_order_fixed.sql`
3. `database/migrations/014_release_promo_on_order_cancel.sql`
4. `database/migrations/preview_store_checkout.sql`
5. `database/migrations/015_route_based_delivery.sql`
6. `database/migrations/016_order_delivery_location.sql`
7. `database/migrations/017_customer_phone_update.sql`
8. `database/migrations/018_fonepay_only_cleanup.sql`
9. `database/migrations/019_fonepay_verified_order_creation.sql`
10. `database/migrations/020_allow_fonepay_checkout_pricing.sql`
11. `database/migrations/021_product_image_storage_bucket.sql`
12. `database/migrations/022_fonepay_whatsapp_order_notifications.sql`
13. `database/migrations/023_whatsapp_webhook_events.sql`
14. `database/migrations/024_fonepay_dynamic_qr.sql`

The checkout migrations define the seven-argument checkout function; the
server-side promotions migration also drops the obsolete six-argument version.
The preview function embedded in `013_create_store_order_fixed.sql` is not
compatible with the current customer and product tables. Apply
`preview_store_checkout.sql` after the numbered checkout migrations and before
015 so it installs the compatible preview function required for route-priced
checkout. Apply the scripts in the listed order on a fresh database; do not
skip or reorder scripts because of their dependencies.

On an **existing database**, do not replay the full migration history blindly.
Migrations 001–010 contain non-idempotent type/table/policy/constraint
operations, and migration 012 server-side promotions explicitly drops an old
checkout function signature. Migration 011 normalizes existing promotion
codes and creates a case-insensitive unique index, so existing codes that
become duplicates after normalization must be resolved first. Use Supabase
migration history to determine which scripts have already been applied, and
apply only missing migrations after checking their prerequisites.

The checkout scripts were renumbered to remove the duplicate `012_` prefix.
If an existing database already ran a script under its previous filename,
do not rerun its renumbered copy solely because the filename changed. Confirm
which database changes are already present and apply only missing steps.

Migrations 015–017 are designed to be rerunnable and do not intentionally drop
functions, tables, or stored data. Migration 015 validates its checkout and
preview prerequisites and creates separate route-priced RPCs, without replacing
the original RPCs. Migration 016 requires 015, adds nullable order coordinates
and a non-validating pair/range constraint, and saves coordinates atomically
with order creation. Migration 017 requires the `profiles` and `customers`
tables and synchronizes their phone values through a service-role-only RPC.
Conflicting existing objects cause an explicit error rather than being
silently replaced. The admin delivery setting is the base fare; the
per-kilometer rate is fixed at NPR 50. The admin order detail page provides an
"Open in Google Maps" link using the saved coordinates.

Migration 019 supports the legacy hosted Fonepay checkout. Migration 020
updates the route-priced checkout and preview functions created by migration
015 so they accept the Fonepay payment method; apply it after 019 on existing
databases.
Migration 021 creates the public-read `product-images` Storage bucket with
staff-only write policies; apply it when product image uploads use Supabase
Storage. Migration 022 installs service-role-only notification claims so
duplicate Fonepay callbacks do not send duplicate WhatsApp order alerts.

## Admin WhatsApp order notifications

After Fonepay verifies payment for a pending order, the backend can send the
admin a WhatsApp Cloud API message with customer contact/address,
Google Maps coordinates, ordered products and quantities, total, paid amount,
and remaining balance. Full payments are labeled `PAID`; deposits are labeled
`PARTIALLY PAID`. Notification delivery is best-effort: a WhatsApp/API failure
is logged and does not undo a verified payment or finalized order. The
notification claim is released after failures and leased for five minutes to
avoid duplicates from concurrent or retried payment callbacks.

Apply migration 022, then configure `WHATSAPP_CLOUD_API_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, and `WHATSAPP_ADMIN_PHONE` in the backend
environment. Keep the token secret; never put it in frontend code or commit it.
The recipient must have opted in. Free-form text messages require an open
WhatsApp customer-service window with the business. To notify outside that
window, create an approved Meta template with one body text parameter and set
`WHATSAPP_TEMPLATE_NAME` and `WHATSAPP_TEMPLATE_LANGUAGE`; the order details
are sent as that parameter in messages kept below the template parameter size
limit.

## Auth-page Cloudflare Turnstile

Login, account registration, and password-reset requests use Cloudflare
Turnstile and pass its token to Supabase Auth. In Cloudflare, make sure the
Turnstile site key allows `onlineliquorland.com` and the hostname used during
local testing (`127.0.0.1` or `localhost`). In Supabase Dashboard, enable
Captcha protection for Auth with the Turnstile provider and configure the
matching Turnstile secret key. The site key in the HTML is public; never put
the secret key in frontend code. Error `110200` means the hostname is not
authorized, while `300*` and `600*` indicate a generic challenge failure that
can be caused by bot detection, browser extensions, VPNs, proxies, or network
filtering. Automated browser testing may also trigger bot detection.

## Fonepay Dynamic QR checkout

Checkout creates a pending Fonepay order, requests a Dynamic QR from the
backend, and displays the returned QR image. The backend signs provider
requests with HMAC-SHA512, verifies signed callbacks, and queries Fonepay's
transaction-verification endpoint before changing the order payment status.
Authenticated customers can retry or manually verify payment from their orders
page. Merchant credentials are never sent to the browser.

Apply `database/migrations/024_fonepay_dynamic_qr.sql` after migrations 001–023
before deploying the Dynamic QR checkout. Configure these backend environment
variables:

- `FONEPAY_MERCHANT_CODE`, `FONEPAY_MERCHANT_SECRET`, `FONEPAY_USERNAME`, and
  `FONEPAY_PASSWORD`: merchant credentials from Fonepay.
- `FONEPAY_API_BASE_URL`: use
  `https://dev-merchantapi.fonepay.com/convergentmerchantweb/api` for UAT, or
  `https://merchantapi.fonepay.com/api` for production.
- `FONEPAY_QR_GENERATE_PATH` defaults to
  `/merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrDownload`.
- `FONEPAY_QR_VERIFY_PATH` defaults to
  `/merchant/merchantDetailsForThirdParty/txnVerification`.

Register `https://onlineliquorland.com/api/payments/fonepay-callback` as the
callback URL in Fonepay's merchant configuration. The QR
generation request signature is HMAC-SHA512 of
`amount,prn,merchantCode,remarks1,remarks2`; the status request signs
`prn,merchantCode`; and the callback signature is checked against
`prn,amount,bid,uid,status,msg,merchantCode`. The HMAC is sent as the
`dataValidation` field. Keep the shared secret, username, and password in
Render's backend environment only.

The `fonepay_qr_payments` table prevents duplicate application of a callback
and allows only one pending QR attempt per order. A successful provider status
check is required before payment is recorded. Failed or abandoned pending
orders reserve stock under the existing order workflow; cancel them through
the normal order cancellation flow if they will not be paid.

## WhatsApp Cloud API webhooks

The backend exposes a single WhatsApp Cloud API callback:
`https://onlineliquorland.com/api/webhooks/whatsapp`. Meta verifies it using
`GET` query parameters `hub.mode`, `hub.verify_token`, and `hub.challenge`.
Webhook `POST` requests must include a valid `X-Hub-Signature-256` HMAC made
with the Meta app secret. Incoming messages and message status changes
(including sent, delivered, read, and failed) are normalized and stored in
`whatsapp_webhook_events`. Event keys are unique, so retried deliveries do not
create duplicate rows. Payloads are limited in size, and the event table is
restricted to the backend service role by RLS and SQL grants.

On an existing Supabase database, apply
`database/migrations/023_whatsapp_webhook_events.sql` before deploying the
webhook code. Configure these values in the Render backend environment, never
in frontend code or Git:

- `WHATSAPP_APP_SECRET`: App Secret from Meta App settings. Used only to verify
  signed webhook POSTs.
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN`: a separate long random string you choose and
  enter in both Render and the Meta Webhooks setup. It is not the Cloud API
  access token.
- `WHATSAPP_PHONE_NUMBER_ID`: the phone number ID already used for outbound
  notifications. Required for inbound webhooks; events for other IDs are ignored.
- `WHATSAPP_BUSINESS_ACCOUNT_ID`: the WABA ID from WhatsApp Manager. Required
  to restrict accepted events to this business account.
- `WHATSAPP_CLOUD_API_TOKEN`: needed for existing outbound order alerts only;
  it is not used to authenticate webhook requests.

In Meta Developers, select the WhatsApp product, configure the callback URL
above and the exact `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, then subscribe the app to
the `messages` webhook field. The production URL must be publicly reachable
over HTTPS. After changing Render environment variables, redeploy/restart the
backend. GET verification returns 503 until the verify token is configured;
POST delivery returns 503 until the app secret, phone number ID, and business
account ID are configured. The callback currently returns 404 on the live site
until this backend change is deployed.

## Customer information pages and SEO

The public storefront includes About Us, Contact Us, Privacy Policy, and Terms
and Conditions pages. Set `STORE_PUBLIC_EMAIL` and/or `STORE_CONTACT_PHONE` in
the backend environment to control the public contact details. `STORE_CONTACT_EMAIL`
is used for geocoding requests and is not published as a customer contact address.
Set `SEO_SITE_URL` to the canonical production origin; it is used by
`/robots.txt` and `/sitemap.xml`. The HTML canonical and structured-data URLs
currently use `https://onlineliquorland.com`; update those URLs if
the store moves to a different public domain.

The storefront includes page metadata, social sharing tags, structured data,
and a sitemap containing the main public pages and every active product
detail URL. Product detail URLs use a slug derived from the product name (for
example, `/product/blue-label-whisky`). The robots file permits product
crawling and points crawlers to the sitemap. Search engines control their own
crawling and ranking, so no implementation can guarantee a particular SEO
score or ranking. Review the Privacy Policy and Terms and Conditions against
the store's actual practices and applicable local law before relying on them
as legal documents.

The public OSM-hosted Nominatim, tile, and OSRM services have usage policies,
rate limits, and no production availability guarantee. The application limits
Nominatim requests to one per second per backend process and searches only when
the customer submits a query. For production traffic, configure reliable
OSM-compatible hosted services or self-host them, and review their attribution
and usage requirements.

Checkout accepts delivery addresses within a 9 km straight-line radius of the
configured store origin. Delivery pricing uses the shortest walking
route. The backend enforces the radius during both price preview and payment
preparation; the browser cannot bypass the limit.

## Staff order access

Active accounts with the `staff` role can access the admin order list and order
details, including customer contact/delivery information and order items. Staff
can update order and payment status. Dashboard, product, customer-management,
delivery-settings, and other administrative APIs remain admin-only.
