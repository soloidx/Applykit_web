"""Header-based CSRF validation for the multipart upload route.

``CsrfViewMiddleware`` reads ``request.POST`` to find the form token, which
would parse and spool a multipart body before the route has rechecked
authentication, consent, and admission. The upload view is therefore
CSRF-exempt and enforces the same origin, referer, cookie, and token checks
from the ``X-CSRFToken`` header before anything reads the body.
"""

from __future__ import annotations

import string
from collections.abc import Callable
from functools import wraps
from typing import cast
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, HttpResponse
from django.utils.crypto import constant_time_compare
from django.utils.http import is_same_domain

__all__ = ["header_csrf_protect"]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_ALLOWED_CHARS = string.ascii_letters + string.digits
_SECRET_LENGTH = 32
_TOKEN_LENGTH = 2 * _SECRET_LENGTH


def header_csrf_protect[View: Callable[..., HttpResponse]](view: View) -> View:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if request.method in _SAFE_METHODS or getattr(request, "_dont_enforce_csrf_checks", False):
            return view(request, *args, **kwargs)
        if not _origin_verified(request) or not _token_matches(request):
            return HttpResponse("CSRF verification failed.", status=403)
        request.csrf_processing_done = True  # type: ignore[attr-defined]
        return view(request, *args, **kwargs)

    # The route performs its own check, so the standard middleware must not
    # touch the body first.
    setattr(wrapped, "csrf_exempt", True)  # noqa: B010
    return cast(View, wrapped)


def _token_matches(request: HttpRequest) -> bool:
    secret = _cookie_secret(request)
    if secret is None:
        return False
    token = request.META.get(settings.CSRF_HEADER_NAME, "")
    if not _valid_token(token):
        return False
    if len(token) == _TOKEN_LENGTH:
        token = _unmask(token)
    return constant_time_compare(token, secret)


def _cookie_secret(request: HttpRequest) -> str | None:
    if settings.CSRF_USE_SESSIONS:
        try:
            secret = request.session.get("_csrftoken")
        except AttributeError:
            return None
    else:
        secret = request.COOKIES.get(settings.CSRF_COOKIE_NAME)
    if not isinstance(secret, str) or not _valid_token(secret):
        return None
    if len(secret) == _TOKEN_LENGTH:
        secret = _unmask(secret)
    return secret


def _valid_token(token: object) -> bool:
    return (
        isinstance(token, str)
        and len(token) in (_SECRET_LENGTH, _TOKEN_LENGTH)
        and all(character in _ALLOWED_CHARS for character in token)
    )


def _unmask(token: str) -> str:
    mask = token[:_SECRET_LENGTH]
    body = token[_SECRET_LENGTH:]
    length = len(_ALLOWED_CHARS)
    return "".join(
        _ALLOWED_CHARS[(_ALLOWED_CHARS.index(value) - _ALLOWED_CHARS.index(offset)) % length]
        for value, offset in zip(body, mask, strict=True)
    )


def _origin_verified(request: HttpRequest) -> bool:
    origin = request.META.get("HTTP_ORIGIN")
    if origin is not None:
        return _origin_allowed(request, origin)
    if request.is_secure():
        return _referer_allowed(request)
    return True


def _origin_allowed(request: HttpRequest, origin: str) -> bool:
    try:
        good_host = request.get_host()
    except DisallowedHost:
        pass
    else:
        scheme = "https" if request.is_secure() else "http"
        if origin == f"{scheme}://{good_host}":
            return True

    trusted = settings.CSRF_TRUSTED_ORIGINS
    if origin in {item for item in trusted if "*" not in item}:
        return True

    parsed = urlsplit(origin)
    for item in trusted:
        if "*" not in item:
            continue
        trusted_origin = urlsplit(item)
        if trusted_origin.scheme != parsed.scheme:
            continue
        if is_same_domain(parsed.netloc, trusted_origin.netloc.lstrip("*")):
            return True
    return False


def _referer_allowed(request: HttpRequest) -> bool:
    referer = request.META.get("HTTP_REFERER")
    if referer is None:
        return False
    parsed = urlsplit(referer)
    if "" in (parsed.scheme, parsed.netloc):
        return False
    if parsed.scheme != "https":
        return False
    for item in settings.CSRF_TRUSTED_ORIGINS:
        if is_same_domain(parsed.netloc, urlsplit(item).netloc.lstrip("*")):
            return True
    good_referer = (
        settings.SESSION_COOKIE_DOMAIN
        if settings.CSRF_USE_SESSIONS
        else settings.CSRF_COOKIE_DOMAIN
    )
    if good_referer is None:
        try:
            good_referer = request.get_host()
        except DisallowedHost:
            return False
    else:
        port = request.get_port()
        if port not in ("443", "80"):
            good_referer = f"{good_referer}:{port}"
    return is_same_domain(parsed.netloc, good_referer)
