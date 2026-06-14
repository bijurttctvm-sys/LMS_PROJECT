"""
Django settings for lms_project.

A Django-based AI-powered multilingual LMS (English & Malayalam).
All credentials are read from environment variables via python-dotenv.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from a .env file at the project root.
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(',') if item.strip()]


def append_unique(values, *extras):
    seen = {value.lower() for value in values}
    for extra in extras:
        if not extra:
            continue
        normalised = extra.strip()
        if not normalised:
            continue
        key = normalised.lower()
        if key in seen:
            continue
        values.append(normalised)
        seen.add(key)
    return values


def env_url_path(name, default):
    value = (os.environ.get(name, default) or default).strip()
    if not value.startswith('/'):
        value = f'/{value.lstrip("/")}'
    if not value.endswith('/'):
        value = f'{value}/'
    return value


APP_ENV = os.environ.get('APP_ENV', 'development').strip().lower()
IS_PRODUCTION = APP_ENV == 'production'
RENDER_SERVICE_TYPE = os.environ.get('RENDER_SERVICE_TYPE', '').strip().lower()
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip()
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL', '').strip()
IS_HTTP_SERVICE = RENDER_SERVICE_TYPE in {'', 'web', 'pserv'}

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool('DEBUG', default=not IS_PRODUCTION)
if IS_PRODUCTION and DEBUG:
    raise ImproperlyConfigured('DEBUG must be disabled when APP_ENV=production.')

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-key'
    else:
        raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG is disabled.')
if not DEBUG and SECRET_KEY.startswith('django-insecure'):
    raise ImproperlyConfigured('Set a unique SECRET_KEY before running without DEBUG.')

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1' if DEBUG else '')
append_unique(ALLOWED_HOSTS, RENDER_EXTERNAL_HOSTNAME)
if not DEBUG and not ALLOWED_HOSTS and not IS_HTTP_SERVICE:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must be configured when DEBUG is disabled.')

CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')
append_unique(CSRF_TRUSTED_ORIGINS, RENDER_EXTERNAL_URL)


# Application definition

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'django_celery_results',
]

LOCAL_APPS = [
    'users',
    'videos',
    'courses',
    'chatbot',
    'doubt_sessions',
    'quizzes',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lms_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'chatbot.context_processors.learning_assistant_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'lms_project.wsgi.application'


# Database
# PostgreSQL via dj-database-url; falls back to local SQLite when DATABASE_URL
# is unset or empty so the project runs out of the box.
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == 'test'

if RUNNING_TESTS:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'test_db.sqlite3',
        }
    }
else:
    DATABASE_URL = os.environ.get('DATABASE_URL') or f'sqlite:///{BASE_DIR / "db.sqlite3"}'
    DATABASES = {
        'default': dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=DATABASE_URL.startswith('postgresql') or DATABASE_URL.startswith('postgres'),
        )
    }


# Custom user model
AUTH_USER_MODEL = 'users.User'


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {'NAME': 'users.password_validators.StrongPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ('en', 'English'),
    ('ml', 'Malayalam'),
]


# Static files (CSS, JavaScript, Images) — served via Whitenoise
STATIC_URL = env_url_path('STATIC_URL', '/static/')
STATIC_ROOT = Path(os.environ.get('STATIC_ROOT', str(BASE_DIR / 'staticfiles')))
STATICFILES_DIRS = [BASE_DIR / 'static'] if (BASE_DIR / 'static').exists() else []

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

# Media files (user uploads)
MEDIA_URL = env_url_path('MEDIA_URL', '/media/')
MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', str(BASE_DIR / 'media')))
SERVE_MEDIA = env_bool('SERVE_MEDIA', default=DEBUG)


# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Authentication redirects
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = 'login'


CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'lms-project-default-cache',
    }
}

LOGIN_FAILURE_LIMIT = int(os.environ.get('LOGIN_FAILURE_LIMIT', '5'))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get('LOGIN_LOCKOUT_SECONDS', '300'))
MAX_VIDEO_UPLOAD_BYTES = int(os.environ.get('MAX_VIDEO_UPLOAD_BYTES', str(2 * 1024 * 1024 * 1024)))
MAX_STUDY_MATERIAL_BYTES = int(os.environ.get('MAX_STUDY_MATERIAL_BYTES', str(50 * 1024 * 1024)))
MAX_PROFILE_IMAGE_BYTES = int(os.environ.get('MAX_PROFILE_IMAGE_BYTES', str(5 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get('FILE_UPLOAD_MAX_MEMORY_SIZE', str(10 * 1024 * 1024))
)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    os.environ.get('DATA_UPLOAD_MAX_MEMORY_SIZE', str(12 * 1024 * 1024))
)
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(
    os.environ.get('DATA_UPLOAD_MAX_NUMBER_FIELDS', '1000')
)

SECURITY_FLAGS_ENABLED = IS_PRODUCTION and not RUNNING_TESTS
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', str(12 * 60 * 60)))
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool(
    'SESSION_EXPIRE_AT_BROWSER_CLOSE',
    default=False,
)
SESSION_SAVE_EVERY_REQUEST = env_bool('SESSION_SAVE_EVERY_REQUEST', default=False)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = env_bool('SESSION_COOKIE_SECURE', default=SECURITY_FLAGS_ENABLED)
SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
CSRF_COOKIE_HTTPONLY = env_bool('CSRF_COOKIE_HTTPONLY', default=True)
CSRF_COOKIE_SECURE = env_bool('CSRF_COOKIE_SECURE', default=SECURITY_FLAGS_ENABLED)
CSRF_COOKIE_SAMESITE = os.environ.get('CSRF_COOKIE_SAMESITE', 'Lax')
SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', default=SECURITY_FLAGS_ENABLED)
SECURE_HSTS_SECONDS = int(
    os.environ.get('SECURE_HSTS_SECONDS', '31536000' if SECURITY_FLAGS_ENABLED else '0')
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(
    'SECURE_HSTS_INCLUDE_SUBDOMAINS',
    default=SECURITY_FLAGS_ENABLED,
)
SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = os.environ.get('SECURE_REFERRER_POLICY', 'same-origin')
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.environ.get(
    'SECURE_CROSS_ORIGIN_OPENER_POLICY',
    'same-origin',
)
SECURE_CROSS_ORIGIN_RESOURCE_POLICY = os.environ.get(
    'SECURE_CROSS_ORIGIN_RESOURCE_POLICY',
    'same-site',
)
X_FRAME_OPTIONS = os.environ.get('X_FRAME_OPTIONS', 'DENY')

if env_bool('USE_X_FORWARDED_PROTO', default=IS_PRODUCTION):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# Celery configuration
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'django-cache'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
# In local development we default Celery to eager mode so uploads, chunking,
# quiz generation, and emails still work without a running Redis/Celery stack.
CELERY_TASK_ALWAYS_EAGER = env_bool('CELERY_TASK_ALWAYS_EAGER', default=DEBUG)
CELERY_TASK_EAGER_PROPAGATES = env_bool('CELERY_TASK_EAGER_PROPAGATES', default=DEBUG)


# Cloudflare R2 (S3-compatible) object storage
CLOUDFLARE_R2_ACCESS_KEY_ID = os.environ.get('CLOUDFLARE_R2_ACCESS_KEY_ID')
CLOUDFLARE_R2_SECRET_ACCESS_KEY = os.environ.get('CLOUDFLARE_R2_SECRET_ACCESS_KEY')
CLOUDFLARE_R2_BUCKET_NAME = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME')
CLOUDFLARE_R2_ENDPOINT_URL = (
    os.environ.get('CLOUDFLARE_R2_ENDPOINT_URL')
    or os.environ.get('CLOUDFLARE_R2_ENDPOINT')
)

# Modal (serverless GPU) tokens
MODAL_TOKEN_ID = os.environ.get('MODAL_TOKEN_ID')
MODAL_TOKEN_SECRET = os.environ.get('MODAL_TOKEN_SECRET')

# Vector store
PINECONE_API_KEY = os.environ.get('PINECONE_API_KEY')
PINECONE_INDEX_NAME = os.environ.get('PINECONE_INDEX_NAME')

# LLM / speech providers
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
SARVAM_API_KEY = os.environ.get('SARVAM_API_KEY')

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
REDIS_CACHE_URL = os.environ.get('REDIS_CACHE_URL', REDIS_URL)

CHATBOT_TOP_K = int(os.environ.get('CHATBOT_TOP_K', '3'))
CHATBOT_MAX_CONTEXT_CHUNKS = int(
    os.environ.get('CHATBOT_MAX_CONTEXT_CHUNKS', str(CHATBOT_TOP_K))
)
CHATBOT_CONTEXT_CHARS_PER_CHUNK = int(
    os.environ.get('CHATBOT_CONTEXT_CHARS_PER_CHUNK', '700')
)
CHATBOT_MAX_TOKENS = int(os.environ.get('CHATBOT_MAX_TOKENS', '384'))
CHATBOT_MAX_QUERY_CHARS = int(os.environ.get('CHATBOT_MAX_QUERY_CHARS', '1000'))
CHATBOT_MAX_TTS_CHARS = int(os.environ.get('CHATBOT_MAX_TTS_CHARS', '500'))
CHATBOT_EMBEDDINGS_REMOTE_FIRST = env_bool('CHATBOT_EMBEDDINGS_REMOTE_FIRST', default=True)
CHATBOT_REMOTE_FAILURE_COOLDOWN = int(
    os.environ.get('CHATBOT_REMOTE_FAILURE_COOLDOWN', '60')
)
CHATBOT_WARMUP_ENABLED = env_bool('CHATBOT_WARMUP_ENABLED', default=not RUNNING_TESTS)
CHATBOT_TRANSLATION_CACHE_TTL = int(
    os.environ.get('CHATBOT_TRANSLATION_CACHE_TTL', '86400')
)
CHATBOT_TTS_CACHE_TTL = int(os.environ.get('CHATBOT_TTS_CACHE_TTL', '86400'))
VIDEO_PROCESSING_TIMEOUT_SECONDS = int(
    os.environ.get(
        'VIDEO_PROCESSING_TIMEOUT_SECONDS',
        '900' if IS_PRODUCTION else '180',
    )
)


# Email
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'no-reply@lms.local')
