from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from publicsuffix2 import get_sld

from apps.applications.models import Company, CompanyDomainAlias, JobApplication


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
    return Company.objects.get_or_create(
        canonical_domain=domain,
        defaults={"name": company_name},
    )


def merge_companies(*, survivor: Company, duplicate: Company) -> None:
    if survivor.pk == duplicate.pk:
        raise ValidationError("Choose a different company to merge into.")

    with transaction.atomic():
        companies = {
            company.pk: company
            for company in Company.objects.select_for_update().filter(
                pk__in=[survivor.pk, duplicate.pk]
            )
        }
        locked_survivor = companies[survivor.pk]
        locked_duplicate = companies[duplicate.pk]
        duplicate_domains = list(
            CompanyDomainAlias.objects.filter(company=locked_duplicate).values_list(
                "domain", flat=True
            )
        )
        if locked_duplicate.canonical_domain:
            duplicate_domains.append(locked_duplicate.canonical_domain)

        JobApplication.objects.filter(company=locked_duplicate).update(company=locked_survivor)
        CompanyDomainAlias.objects.filter(company=locked_duplicate).delete()
        locked_duplicate.delete()
        existing_domains = set(
            CompanyDomainAlias.objects.filter(company=locked_survivor).values_list(
                "domain", flat=True
            )
        )
        for domain in duplicate_domains:
            if domain != locked_survivor.canonical_domain and domain not in existing_domains:
                CompanyDomainAlias.objects.create(company=locked_survivor, domain=domain)
                existing_domains.add(domain)
