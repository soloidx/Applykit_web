from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db.models import Q
from publicsuffix2 import get_sld

from apps.applications.models import Company


def normalized_registrable_domain(website: str) -> str:
    value = website.strip()
    parsed = urlsplit(value if "://" in value else f"//{value}")
    hostname = parsed.hostname
    if not hostname:
        raise ValidationError("Enter a website with a hostname.")

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValidationError("Enter a valid website hostname.") from error

    domain = get_sld(ascii_hostname, strict=True)
    if domain is None:
        raise ValidationError("Enter a website with a registrable domain.")
    return domain


def create_or_reuse_company(name: str, website: str | None = None) -> tuple[Company, bool]:
    company_name = name.strip()
    if not company_name:
        raise ValidationError("Enter a company name.")
    if not website:
        return Company.objects.create(name=company_name), True

    domain = normalized_registrable_domain(website)
    existing = (
        Company.objects.filter(Q(canonical_domain=domain) | Q(domain_aliases__domain=domain))
        .order_by("pk")
        .first()
    )
    if existing:
        return existing, False
    return Company.objects.create(name=company_name, canonical_domain=domain), True
