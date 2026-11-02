import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv(
        "SUPABASE_SERVICE_ROLE_KEY",
        "",
    )
    OSM_NOMINATIM_URL: str = os.getenv(
        "OSM_NOMINATIM_URL",
        "https://nominatim.openstreetmap.org",
    ).rstrip("/")
    OSM_ROUTING_URL: str = os.getenv(
        "OSM_ROUTING_URL",
        "https://router.project-osrm.org",
    ).rstrip("/")
    OSM_ROUTING_FALLBACK_URL: str = os.getenv(
        "OSM_ROUTING_FALLBACK_URL",
        "",
    ).rstrip("/")
    STORE_CONTACT_EMAIL: str = os.getenv(
        "STORE_CONTACT_EMAIL",
        "",
    )
    STORE_PUBLIC_EMAIL: str = os.getenv(
        "STORE_PUBLIC_EMAIL",
        "",
    )
    STORE_CONTACT_PHONE: str = os.getenv(
        "STORE_CONTACT_PHONE",
        "",
    )
    STORE_PUBLIC_URL: str = os.getenv(
        "STORE_PUBLIC_URL",
        "https://online-liquor-land.onrender.com",
    ).rstrip("/")
    SEO_SITE_URL: str = os.getenv(
        "SEO_SITE_URL",
        "https://online-liquor-land.onrender.com",
    ).rstrip("/")
    STORE_ORIGIN_LATITUDE: str = os.getenv(
        "STORE_ORIGIN_LATITUDE",
        "",
    )
    STORE_ORIGIN_LONGITUDE: str = os.getenv(
        "STORE_ORIGIN_LONGITUDE",
        "",
    )
    FONEPAY_MERCHANT_ID: str = os.getenv(
        "FONEPAY_MERCHANT_ID",
        "",
    )
    FONEPAY_MERCHANT_SECRET: str = os.getenv(
        "FONEPAY_MERCHANT_SECRET",
        "",
    )
    FONEPAY_FORM_URL: str = os.getenv(
        "FONEPAY_FORM_URL",
        "https://sandbox.fonepay.com.np/api/payment",
    ).rstrip("/")
    FONEPAY_VERIFY_URL: str = os.getenv(
        "FONEPAY_VERIFY_URL",
        "https://sandbox.fonepay.com.np/api/verify",
    ).rstrip("/")
    MINIMUM_INITIAL_PAYMENT: str = os.getenv(
        "MINIMUM_INITIAL_PAYMENT",
        "300",
    )
    WHATSAPP_CLOUD_API_TOKEN: str = os.getenv(
        "WHATSAPP_CLOUD_API_TOKEN",
        "",
    )
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv(
        "WHATSAPP_PHONE_NUMBER_ID",
        "",
    )
    WHATSAPP_ADMIN_PHONE: str = os.getenv(
        "WHATSAPP_ADMIN_PHONE",
        "+9779851144738",
    )
    WHATSAPP_TEMPLATE_NAME: str = os.getenv(
        "WHATSAPP_TEMPLATE_NAME",
        "",
    )
    WHATSAPP_TEMPLATE_LANGUAGE: str = os.getenv(
        "WHATSAPP_TEMPLATE_LANGUAGE",
        "en",
    )
    WHATSAPP_GRAPH_API_VERSION: str = os.getenv(
        "WHATSAPP_GRAPH_API_VERSION",
        "v22.0",
    )

    def validate(self):
        missing = []

        if not self.SUPABASE_URL:
            missing.append("SUPABASE_URL")

        if not self.SUPABASE_ANON_KEY:
            missing.append("SUPABASE_ANON_KEY")

        if not self.SUPABASE_SERVICE_ROLE_KEY:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")

        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
            )


settings = Settings()
