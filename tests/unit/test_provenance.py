import pytest

from apps.applications.provenance import (
    PostingUrlRejected,
    normalized_posting_url,
    sanitize_posting_url,
)


def test_accepts_http_and_https():
    assert sanitize_posting_url("https://example.com/jobs/1") == "https://example.com/jobs/1"
    assert sanitize_posting_url("http://example.com/jobs/1") == "http://example.com/jobs/1"


def test_blank_is_allowed():
    assert sanitize_posting_url("") == ""
    assert sanitize_posting_url("   ") == ""


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.com/jobs",
        "javascript:alert(1)",
        "example.com/jobs",
        "//example.com/jobs",
        "https://",
    ],
)
def test_rejects_non_http_urls(value: str):
    with pytest.raises(PostingUrlRejected):
        sanitize_posting_url(value)


def test_rejects_malformed_urls_instead_of_raising_a_value_error():
    with pytest.raises(PostingUrlRejected):
        sanitize_posting_url("https://[::1")


def test_strips_fragments():
    assert sanitize_posting_url("https://example.com/jobs/1#apply") == "https://example.com/jobs/1"


def test_strips_known_tracking_parameters_and_keeps_the_rest():
    sanitized = sanitize_posting_url(
        "https://example.com/jobs/1?utm_source=linkedin&utm_campaign=x&gclid=abc&id=7"
    )
    assert sanitized == "https://example.com/jobs/1?id=7"


@pytest.mark.parametrize(
    "value",
    [
        "https://user:secret@example.com/jobs/1",
        "https://token@example.com/jobs/1",
    ],
)
def test_rejects_embedded_credentials(value: str):
    with pytest.raises(PostingUrlRejected):
        sanitize_posting_url(value)


@pytest.mark.parametrize(
    "name",
    [
        "token",
        "access_token",
        "auth",
        "api_key",
        "apikey",
        "session",
        "signature",
        "sig",
        "jwt",
        "id_token",
        "refresh_token",
        "password",
        "secret",
        "sso_token",
    ],
)
def test_rejects_apparent_sensitive_access_tokens(name: str):
    with pytest.raises(PostingUrlRejected):
        sanitize_posting_url(f"https://example.com/jobs/1?{name}=abc123")


def test_lowercases_scheme_and_host_and_drops_default_ports():
    assert sanitize_posting_url("HTTPS://Example.COM:443/jobs/1") == "https://example.com/jobs/1"
    assert sanitize_posting_url("http://Example.COM:80/jobs/1") == "http://example.com/jobs/1"


def test_keeps_non_default_ports():
    assert (
        sanitize_posting_url("https://example.com:8443/jobs/1") == "https://example.com:8443/jobs/1"
    )


def test_rejects_over_limit_urls_without_truncation():
    with pytest.raises(PostingUrlRejected):
        sanitize_posting_url("https://example.com/" + "a" * 3000)


def test_normalized_url_ignores_scheme_tracking_fragment_and_case():
    assert normalized_posting_url(
        "https://Example.com:443/jobs/1/?utm_source=x#apply"
    ) == normalized_posting_url("http://example.com/jobs/1")


def test_normalized_url_ignores_a_leading_www_label():
    assert normalized_posting_url("https://www.example.com/jobs/1") == normalized_posting_url(
        "https://example.com/jobs/1"
    )


def test_normalized_url_distinguishes_different_postings():
    assert normalized_posting_url("https://example.com/jobs/1") != normalized_posting_url(
        "https://example.com/jobs/2"
    )


def test_normalized_url_is_blank_for_blank_or_unsafe_values():
    assert normalized_posting_url("") == ""
    assert normalized_posting_url("https://user:secret@example.com/jobs") == ""
