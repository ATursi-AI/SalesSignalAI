from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-dev-key-change-in-production')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*', cast=Csv())

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.RoleAccessMiddleware',
]

ROOT_URLCONF = 'salessignal.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.crm_counts',
                'core.context_processors.lead_sidebar_counts',
            ],
        },
    },
]

WSGI_APPLICATION = 'salessignal.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = []
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/auth/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# External service keys
REDDIT_CLIENT_ID = config('REDDIT_CLIENT_ID', default='')
REDDIT_CLIENT_SECRET = config('REDDIT_CLIENT_SECRET', default='')
REDDIT_USER_AGENT = config('REDDIT_USER_AGENT', default='SalesSignalAI/1.0')
REDDIT_USERNAME = config('REDDIT_USERNAME', default='')
REDDIT_PASSWORD = config('REDDIT_PASSWORD', default='')

GOOGLE_MAPS_API_KEY = config('GOOGLE_MAPS_API_KEY', default='')
GOOGLE_PLACES_API_KEY = config('GOOGLE_PLACES_API_KEY', default='') or GOOGLE_MAPS_API_KEY
ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')
SENDGRID_API_KEY = config('SENDGRID_API_KEY', default='')

# Telegram
TELEGRAM_BOT_TOKEN = config('TELEGRAM_BOT_TOKEN', default='')
TELEGRAM_ALLOWED_USERS = config('TELEGRAM_ALLOWED_USERS', default='')

TWILIO_ACCOUNT_SID = config('TWILIO_ACCOUNT_SID', default='')
TWILIO_AUTH_TOKEN = config('TWILIO_AUTH_TOKEN', default='')
TWILIO_PHONE_NUMBER = config('TWILIO_PHONE_NUMBER', default='')

# SignalWire
SIGNALWIRE_PROJECT_ID = config('SIGNALWIRE_PROJECT_ID', default='')
SIGNALWIRE_API_TOKEN = config('SIGNALWIRE_API_TOKEN', default='')
SIGNALWIRE_SPACE_URL = config('SIGNALWIRE_SPACE_URL', default='')
SIGNALWIRE_PHONE_NUMBER = config('SIGNALWIRE_PHONE_NUMBER', default='')
SIGNALWIRE_FALLBACK_PHONE = config('SIGNALWIRE_FALLBACK_PHONE', default='')
SIGNALWIRE_SMS_NUMBER = config('SIGNALWIRE_SMS_NUMBER', default='')

SERPAPI_KEY = config('SERPAPI_KEY', default='')

# Agent system — phone numbers that can command agents via SMS
AGENT_ADMIN_NUMBERS = [n.strip() for n in config('AGENT_ADMIN_NUMBERS', default='').split(',') if n.strip()]
APIFY_API_TOKEN = config('APIFY_API_TOKEN', default='')
ZEROBOUNCE_API_KEY = config('ZEROBOUNCE_API_KEY', default='')

ALERT_FROM_EMAIL = config('ALERT_FROM_EMAIL', default='alerts@salessignal.ai')

# Nextdoor credentials (Playwright browser automation)
NEXTDOOR_EMAIL = config('NEXTDOOR_EMAIL', default='')
NEXTDOOR_PASSWORD = config('NEXTDOOR_PASSWORD', default='')

# Lead ingestion API
INGEST_API_KEY = config('INGEST_API_KEY', default='')
REMOTE_INGEST_URL = config('REMOTE_INGEST_URL', default='https://salessignalai.com/api/ingest-lead/')

# Stripe
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
STRIPE_PUBLISHABLE_KEY = config('STRIPE_PUBLISHABLE_KEY', default='')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET', default='')
STRIPE_SETUP_FEE_PRICE_ID = config('STRIPE_SETUP_FEE_PRICE_ID', default='')

# Stripe plan price IDs
STRIPE_PRICE_OUTREACH = config('STRIPE_PRICE_OUTREACH', default='')
STRIPE_PRICE_GROWTH = config('STRIPE_PRICE_GROWTH', default='')
STRIPE_PRICE_DOMINATE = config('STRIPE_PRICE_DOMINATE', default='')

SUPPORT_EMAIL = 'support@salessignalai.com'

# Email backend (console for dev, SMTP for prod)
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='smtp.hostinger.com')
EMAIL_PORT = config('EMAIL_PORT', default=465, cast=int)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = SUPPORT_EMAIL

# Outreach email sending backend: 'ses' (default), 'instantly'
OUTREACH_EMAIL_BACKEND = config('OUTREACH_EMAIL_BACKEND', default='ses')

# Amazon SES credentials
AWS_SES_ACCESS_KEY = config('AWS_SES_ACCESS_KEY', default='')
AWS_SES_SECRET_KEY = config('AWS_SES_SECRET_KEY', default='')
AWS_SES_REGION = config('AWS_SES_REGION', default='us-east-1')

# Instantly.ai (future)
INSTANTLY_API_KEY = config('INSTANTLY_API_KEY', default='')

# Google OAuth (Gmail sending for customers)
GOOGLE_OAUTH_CLIENT_ID = config('GOOGLE_OAUTH_CLIENT_ID', default='')
GOOGLE_OAUTH_CLIENT_SECRET = config('GOOGLE_OAUTH_CLIENT_SECRET', default='')

# AI email generation
GEMINI_API_KEY = config('GEMINI_API_KEY', default='')
DEEPSEEK_API_KEY = config('DEEPSEEK_API_KEY', default='')
