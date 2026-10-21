"""Isolated laptop-hosted demo. Launch with public_demo.py, not runserver."""
import os
from pathlib import Path
from .settings import *  # noqa: F403

DEMO_ROOT = BASE_DIR / '.public-demo'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DEMO_ROOT / 'demo.sqlite3'}}
MEDIA_ROOT = DEMO_ROOT / 'private-media'
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
SESSION_COOKIE_NAME = 'dentech_public_session'
CSRF_COOKIE_NAME = 'dentech_public_csrf'
CSRF_TRUSTED_ORIGINS = [PUBLIC_BASE_URL]
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
