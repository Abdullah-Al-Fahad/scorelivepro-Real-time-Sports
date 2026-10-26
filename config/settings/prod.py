from .base import *

DEBUG = False

SECRET_KEY = os.environ.setdefault('SECRET_KEY', '')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

# --- SECURITY & PROXY CONFIGURATION ---
# Fixes CSRF 403 Forbidden by trusting the WSS origin
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[
    'https://api.scorelivepro.it',
    'wss://api.scorelivepro.it'
])

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# --- DATABASE RESILIENCE & DEADLOCK PREVENTION ---
# lock_timeout=10000ms: Abort transactions waiting on locks > 10s to prevent server freezes
# statement_timeout=30000ms: Guard against runaway unindexed queries
if 'default' in DATABASES:
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS']['options'] = '-c lock_timeout=10000 -c statement_timeout=30000'
    DATABASES['default']['CONN_MAX_AGE'] = 60

# Explicitly use the Redis broker for Channels
CELERY_BROKER_URL = env('CELERY_BROKER_URL')
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [CELERY_BROKER_URL],
        },
    },
}

# Email Config
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)