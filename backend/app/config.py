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
    FIREBASE_WEB_API_KEY: str = os.getenv(
        "FIREBASE_WEB_API_KEY",
        "",
    ).strip()
    FIREBASE_WEB_AUTH_DOMAIN: str = os.getenv(
        "FIREBASE_WEB_AUTH_DOMAIN",
        "",
    ).strip()
    FIREBASE_WEB_PROJECT_ID: str = os.getenv(
        "FIREBASE_WEB_PROJECT_ID",
        "",
    ).strip()
    FIREBASE_WEB_STORAGE_BUCKET: str = os.getenv(
        "FIREBASE_WEB_STORAGE_BUCKET",
        "",
    ).strip()
    FIREBASE_WEB_MESSAGING_SENDER_ID: str = os.getenv(
        "FIREBASE_WEB_MESSAGING_SENDER_ID",
        "",
    ).strip()
    FIREBASE_WEB_APP_ID: str = os.getenv(
        "FIREBASE_WEB_APP_ID",
        "",
    ).strip()
    FIREBASE_WEB_VAPID_KEY: str = os.getenv(
        "FIREBASE_WEB_VAPID_KEY",
        "",
    ).strip()
    OSM_NOMINATIM_URL: str = os.getenv(
        "OSM_NOMINATIM_URL",
        "https://nominatim.openstreetmap.org",
    ).rstrip("/")
    OSM_ROUTING_URL: str = os.getenv(
        "OSM_ROUTING_URL",
        "https://routing.openstreetmap.de/routed-foot",
    ).rstrip("/")
    OSM_ROUTING_FALLBACK_URL: str = os.getenv(
        "OSM_ROUTING_FALLBACK_URL",
        "",
    ).strip().rstrip("/")
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
        "https://onlineliquorland.com",
    ).rstrip("/")
    SEO_SITE_URL: str = os.getenv(
        "SEO_SITE_URL",
        "https://onlineliquorland.com",
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
    FONEPAY_MERCHANT_CODE: str = os.getenv(
        "FONEPAY_MERCHANT_CODE",
        os.getenv("FONEPAY_MERCHANT_ID", ""),
    ).strip()
    FONEPAY_USERNAME: str = os.getenv(
        "FONEPAY_USERNAME",
        "",
    )
    FONEPAY_PASSWORD: str = os.getenv(
        "FONEPAY_PASSWORD",
        "",
    )
    FONEPAY_API_BASE_URL: str = os.getenv(
        "FONEPAY_API_BASE_URL",
        "https://dev-merchantapi.fonepay.com/convergentmerchantweb/api",
    ).rstrip("/")
    FONEPAY_QR_GENERATE_PATH: str = os.getenv(
        "FONEPAY_QR_GENERATE_PATH",
        "/merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrDownload",
    )
    FONEPAY_QR_VERIFY_PATH: str = os.getenv(
        "FONEPAY_QR_VERIFY_PATH",
        "/merchant/merchantDetailsForThirdParty/txnVerification",
    )
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
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = os.getenv(
        "WHATSAPP_BUSINESS_ACCOUNT_ID",
        "",
    )
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = os.getenv(
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
        "",
    )
    WHATSAPP_APP_SECRET: str = os.getenv(
        "WHATSAPP_APP_SECRET",
        "",
    )
    WHATSAPP_ADMIN_PHONE: str = os.getenv(
        "WHATSAPP_ADMIN_PHONE",
        "",
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

    def firebase_web_config(self):
        return {
            "apiKey": self.FIREBASE_WEB_API_KEY,
            "authDomain": self.FIREBASE_WEB_AUTH_DOMAIN,
            "projectId": self.FIREBASE_WEB_PROJECT_ID,
            "storageBucket": self.FIREBASE_WEB_STORAGE_BUCKET,
            "messagingSenderId": self.FIREBASE_WEB_MESSAGING_SENDER_ID,
            "appId": self.FIREBASE_WEB_APP_ID,
        }

    def firebase_web_configured(self):
        config = self.firebase_web_config()
        return all(
            config[key]
            for key in (
                "apiKey",
                "authDomain",
                "projectId",
                "messagingSenderId",
                "appId",
            )
        ) and bool(self.FIREBASE_WEB_VAPID_KEY)


settings = Settings()
