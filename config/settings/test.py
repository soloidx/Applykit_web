from . import base
from .base import *  # noqa: F403

DEBUG = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MIDDLEWARE = [
    middleware
    for middleware in base.MIDDLEWARE
    if middleware != "whitenoise.middleware.WhiteNoiseMiddleware"
]
