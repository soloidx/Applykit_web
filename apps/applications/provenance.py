"""Private provenance for imported Job Applications.

ApplyKit never retrieves the optional posting URL. It is sanitized locally
before it can be persisted: fragments and known tracking parameters are
removed, and a URL with embedded credentials or an apparent sensitive access
token is rejected rather than stored. Only a reviewed, sanitized URL reaches the
database, where a normalized form gives Account-private duplicate lookups.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = [
    "PostingUrlRejected",
    "normalized_posting_url",
    "sanitize_posting_url",
]

_MAX_URL_LENGTH = 2048

# Query parameters that describe a visit, not the posting itself.
_TRACKING_PARAMETERS = frozenset(
    {
        "_ga",
        "_gl",
        "dclid",
        "fbclid",
        "gclid",
        "igshid",
        "li_fat_id",
        "mc_cid",
        "mc_eid",
        "mkt_tok",
        "msclkid",
        "ref",
        "ref_src",
        "referrer",
        "source",
        "src",
        "trk",
        "trkcampaign",
        "twclid",
        "vero_id",
        "yclid",
    }
)
_TRACKING_PREFIXES = ("hsa_", "oly_", "utm_")

# Parameter names that commonly carry an access token, session, or credential.
_SENSITIVE_PARAMETERS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "authtoken",
        "client_secret",
        "credential",
        "credentials",
        "id_token",
        "jwt",
        "key",
        "oauth_token",
        "passwd",
        "password",
        "refresh_token",
        "secret",
        "session",
        "session_id",
        "sessionid",
        "sig",
        "signature",
        "sso_token",
        "token",
    }
)
_SENSITIVE_SUFFIXES = ("_auth", "_key", "_password", "_sig", "_signature", "_token")


class PostingUrlRejected(Exception):
    """The URL is not a safe HTTP(S) posting URL to persist."""


def sanitize_posting_url(raw: str) -> str:
    """Return the reviewed, sanitized provenance URL or reject it.

    A blank value is allowed: provenance is optional. Fragments and known
    tracking parameters are removed. Embedded credentials and apparent
    sensitive access tokens cause a rejection instead of silent removal.
    """

    value = raw.strip() if isinstance(raw, str) else ""
    if not value:
        return ""
    if len(value) > _MAX_URL_LENGTH:
        raise PostingUrlRejected("the posting URL is too long")

    try:
        parts = urlsplit(value)
    except ValueError:
        raise PostingUrlRejected("the posting URL is malformed") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise PostingUrlRejected("the posting URL is not HTTP(S)")
    hostname = parts.hostname
    if not hostname:
        raise PostingUrlRejected("the posting URL has no host")
    if parts.username is not None or parts.password is not None:
        raise PostingUrlRejected("the posting URL embeds credentials")
    try:
        port = parts.port
    except ValueError:
        raise PostingUrlRejected("the posting URL has an invalid port") from None

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    for name, _value in pairs:
        if _is_sensitive(name):
            raise PostingUrlRejected("the posting URL carries a sensitive access token")

    kept = [(name, value) for name, value in pairs if not _is_tracking(name)]
    host = _format_host(hostname, port, scheme)
    return urlunsplit((scheme, host, parts.path, urlencode(kept), ""))


def normalized_posting_url(value: str) -> str:
    """Return an opaque Account-private duplicate key, or blank when unsafe.

    The same posting can be reached over either scheme, behind a ``www`` label,
    with or without a default port, a trailing slash, a fragment, or tracking
    parameters. Those differences do not make a distinct posting.
    """

    try:
        sanitized = sanitize_posting_url(value)
    except PostingUrlRejected:
        return ""
    if not sanitized:
        return ""

    parts = urlsplit(sanitized)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = f"[{host}]"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    key = f"{host}{parts.path.rstrip('/')}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    if query:
        key = f"{key}?{query}"
    return key


def _format_host(hostname: str, port: int | None, scheme: str) -> str:
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and not _is_default_port(scheme, port):
        return f"{host}:{port}"
    return host


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme, port) in (("http", 80), ("https", 443))


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMETERS or lowered.startswith(_TRACKING_PREFIXES)


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SENSITIVE_PARAMETERS:
        return True
    return lowered.startswith("auth") or lowered.endswith(_SENSITIVE_SUFFIXES)
