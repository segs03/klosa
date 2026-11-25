
"""
Django settings for Klosa Bank.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
from decimal import Decimal


# ============================================================
# 1. PROJECT PATHS AND ENVIRONMENT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DJANGO_ENV = os.environ.get("DJANGO_ENV", "development").strip().lower()
TESTING = "test" in sys.argv

# Development uses the project's .env.
# Production uses /etc/klosa/env.
if DJANGO_ENV == "production":
    load_dotenv("/etc/klosa/env", override=False)
else:
    load_dotenv(BASE_DIR / ".env", override=False)


def env(name, default=""):
    return os.environ.get(name, default)


def flag(name, default=False):
    return env(
        name, "1" if default else "0"
    ).strip().lower() in ("1", "true", "yes", "on")


# ============================================================
# 2. CORE SECURITY SETTINGS
# ============================================================

DEBUG = flag("DJANGO_DEBUG", False)

SECRET_KEY = env("SECRET_KEY")

if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "dev-only-not-a-secret-" + "x" * 40
    else:
        raise ImproperlyConfigured("SECRET_KEY is not set.")

ALLOWED_HOSTS = [
    host.strip()
    for host in env("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in env("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

ADMIN_URL = env("ADMIN_URL", "admin/")

SITE_URL = env("SITE_URL", "http://localhost:8000")


# ============================================================
# 3. APPLICATION SWITCHES
# ============================================================

FEATURES = {
    "bills": flag("FEATURE_BILLS", DEBUG or TESTING),
    "cashier": flag("FEATURE_CASHIER", DEBUG or TESTING),
    "cards": flag("FEATURE_CARDS", DEBUG or TESTING),
    "credit": flag("FEATURE_CREDIT", DEBUG or TESTING),
}

STAFF_2FA_REQUIRED = flag(
    "STAFF_2FA_REQUIRED",
    not (DEBUG or TESTING),
)

TRUST_REAL_IP_HEADER = flag("TRUST_REAL_IP_HEADER", False)


# ============================================================
# 4. PASSWORD AND AUTHENTICATION
# ============================================================

if TESTING:
    PASSWORD_HASHERS = [
        "django.contrib.auth.hashers.MD5PasswordHasher"
    ]

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "accounts.backends.IdentifierBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

FACE_PROVIDER = "accounts.face.ManualReview"      # a provider class with assess(check) -> (status, score, ref)


# ============================================================
# 5. DATABASE
# ============================================================

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "klosadb"),
        "USER": env("DB_USER", "klosa"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "5432"),

        "CONN_MAX_AGE": (
            0 if TESTING
            else int(env("DB_CONN_MAX_AGE", "60"))
        ),

        "CONN_HEALTH_CHECKS": True,

        "OPTIONS": {
            "sslmode": env("DB_SSLMODE", "prefer"),
        },
    }
}


# ============================================================
# 6. CACHE AND SESSIONS
# ============================================================

REDIS_URL = env("REDIS_URL")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

SESSION_COOKIE_AGE = int(env("SESSION_IDLE_SECONDS", "900"))

SESSION_SAVE_EVERY_REQUEST = True

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG


# ============================================================
# 7. HTTPS AND SECURITY HEADERS
# ============================================================

SECURE_SSL_REDIRECT = not DEBUG

SECURE_REDIRECT_EXEMPT = [
    r"^healthz/$",
]

if not DEBUG:
    # Only trust this header when requests pass through
    # your trusted HTTPS reverse proxy.
    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

# Keep HSTS disabled until the production domain and HTTPS
# configuration have been verified.
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0"))

SECURE_HSTS_INCLUDE_SUBDOMAINS = flag(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", False
)

SECURE_HSTS_PRELOAD = False

SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

X_FRAME_OPTIONS = "DENY"


# ============================================================
# 8. IDENTITY AND ENCRYPTION
# ============================================================

IDENTITY_HASH_KEY = env("IDENTITY_HASH_KEY")

TOTP_ENCRYPTION_KEY = env("TOTP_ENCRYPTION_KEY")

# Retained pending confirmation that no other code uses it.
KYC_IDENTITY_HMAC_KEY = env("KYC_IDENTITY_HMAC_KEY")


# ============================================================
# 9. KYC
# ============================================================

KYC_APPROVALS_REQUIRED = 1

KYC_MAX_UPLOAD = 5 * 1024 * 1024

BVN_API_URL = "https://api.withmono.com/v3/lookup/bvn/initiate"   # from your provider

BVN_API_KEY = env("BVN_API_KEY")

FACE_PROVIDER = env("FACE_PROVIDER", "accounts.face.ManualReview")

FACE_MODEL_DIR = env("FACE_MODEL_DIR", str(BASE_DIR / "models"))

# ============================================================
# 10. FILE UPLOADS AND PRIVATE STORAGE
# ============================================================

if DEBUG:
    FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

    FILE_UPLOAD_HANDLERS = [
        "django.core.files.uploadhandler.MemoryFileUploadHandler"
    ]
else:
    FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440

DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024

PRIVATE_MEDIA_ROOT = Path(
    env(
        "PRIVATE_MEDIA_ROOT",
        str(BASE_DIR.parent / "klosa_private_media"),
    )
).expanduser()


# ============================================================
# 11. PROVIDERS AND EXTERNAL SERVICES
# ============================================================

SMS_BACKEND = env("SMS_BACKEND", "console")

BILLER_PROVIDER = env(
    "BILLER_PROVIDER",
    "biller.providers.mock.MockProvider",
)

CASHIER_PROVIDER = env(
    "CASHIER_PROVIDER",
    "cashier.providers.paystack.PaystackProvider",
)

CARDS_ISSUER = env(
    "CARDS_ISSUER",
    "cards.issuer.MockIssuer",
)

CASHIER_WEBHOOK_SECRET = env("CASHIER_WEBHOOK_SECRET")

CARDS_WEBHOOK_SECRET = env("CARDS_WEBHOOK_SECRET")

PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY")

CASHIER_ALLOW_MOCK_PAYPAGE = flag(
    "CASHIER_ALLOW_MOCK_PAYPAGE", DEBUG
)

CARDS_ALLOW_SIMULATOR = flag(
    "CARDS_ALLOW_SIMULATOR", DEBUG
)

CASHIER_WITHDRAW_MIN_TIER = 1

CASHIER_FALLBACK_EMAIL_DOMAIN = env(
    "CASHIER_FALLBACK_EMAIL_DOMAIN",
    "customers.yourdomain.com",
)

CASHIER_INBOUND_TRANSFERS = flag(
    "CASHIER_INBOUND_TRANSFERS", False
)

ALLOW_EXTERNAL_TRANSFERS = flag(
    "ALLOW_EXTERNAL_TRANSFERS", False
)

KLOSA_BANK_CODE = env("KLOSA_BANK_CODE", "999")

COMPANY_BANK = {"bank": env("COMPANY_BANK_NAME"), "name": env("COMPANY_ACCOUNT_NAME"), "number": env("COMPANY_ACCOUNT_NUMBER")}


# ============================================================
# 12. CREDIT FACILITY
# ============================================================

CREDIT_MIN_ACCOUNT_AGE_DAYS = int(
    env("CREDIT_MIN_ACCOUNT_AGE_DAYS", "30")
)

CREDIT_MIN_BALANCE = env("CREDIT_MIN_BALANCE", "10000")

CREDIT_P2P_SINGLE = "50000"    # max per credit-card payment to a person

CREDIT_P2P_DAILY = "100000"    # max per day by credit card


# ============================================================
# 13. WELCOME BONUS
# ============================================================

WELCOME_BONUS_ENABLED = flag("WELCOME_BONUS_ENABLED", True)

WELCOME_BONUS_AMOUNT = Decimal(
    env("WELCOME_BONUS_AMOUNT", "200000")
)

WELCOME_BONUS_BUDGET = Decimal(
    env("WELCOME_BONUS_BUDGET", "20000000")
)


# ============================================================
# 14. APPLICATION DEFINITION
# ============================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "accounts",
    "transactions",
    "cards",
    "transfers",
    "beneficiary",
    "funding",
    "biller",
    "savings",
    "cashier",
    "treasury",
    "backoffice",
    "notices",

    "fontawesomefree",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "accounts.security.SecurityHeadersMiddleware",
    "accounts.security.RealIPMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "accounts.security.StaffTwoFactorMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "accounts.security.FeatureGateMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "bank.urls"

WSGI_APPLICATION = "bank.wsgi.application"


# ============================================================
# 15. TEMPLATES
# ============================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "notices.context.unread",
                "accounts.security.features",
            ],
        },
    },
]


# ============================================================
# 16. STATIC FILES
# ============================================================

STATIC_URL = "/static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

LOGIN_URL = "/accounts/login/"

LOGIN_REDIRECT_URL = "/accounts/"


# ============================================================
# 17. EMAIL
# ============================================================

DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL",
    "Klosa Bank <no-reply@yourdomain.com>",
)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    (
        "django.core.mail.backends.console.EmailBackend"
        if DEBUG
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)

EMAIL_HOST = env("EMAIL_HOST")

EMAIL_PORT = int(env("EMAIL_PORT", "587"))

EMAIL_USE_TLS = flag("EMAIL_USE_TLS", True)

EMAIL_USE_SSL = flag("EMAIL_USE_SSL", False)

EMAIL_HOST_USER = env("EMAIL_HOST_USER")

EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")

EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "10"))


# ============================================================
# 18. WEBAUTHN / PASSKEYS
# ============================================================

WEBAUTHN_RP_ID = env("WEBAUTHN_RP_ID", "localhost")

WEBAUTHN_RP_NAME = env(
    "WEBAUTHN_RP_NAME",
    "Klosa Bank",
)

WEBAUTHN_ORIGIN = env(
    "WEBAUTHN_ORIGIN",
    "http://localhost:8000",
)


# ============================================================
# 19. INTERNATIONALIZATION
# ============================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Lagos"

USE_I18N = True

USE_TZ = True


# ============================================================
# 20. DEFAULT MODEL FIELD
# ============================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============================================================
# 21. LOGGING
# ============================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "standard": {
            "format": (
                "%(asctime)s %(levelname)s "
                "%(name)s %(message)s"
            ),
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },

    "root": {
        "handlers": ["console"],
        "level": env("LOG_LEVEL", "INFO"),
    },

    "loggers": {
        "security": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },

        "kyc.audit": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


MEDIA_URL = "/media/"

MEDIA_ROOT = "/data/data/com.termux/files/home/klosa_media"
