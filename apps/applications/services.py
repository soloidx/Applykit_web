from datetime import datetime
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from publicsuffix2 import get_sld

from apps.accounts.models import Account
from apps.applications.models import (
    Company,
    CompanyDomainAlias,
    JobApplication,
    RecruitmentEvent,
    StageTransition,
)


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


@transaction.atomic
def transition_application(*, account: Account, application_id: int, stage: str) -> JobApplication:
    try:
        target_stage = JobApplication.Stage(stage)
    except ValueError as error:
        raise ValidationError("Choose a supported application stage.") from error

    application = JobApplication.objects.select_for_update().get(
        pk=application_id,
        account=account,
    )
    if application.stage == target_stage:
        raise ValidationError("Choose a different application stage.")

    previous_stage = application.stage
    if target_stage == JobApplication.Stage.SUBMITTED and application.first_submitted_at is None:
        application.first_submitted_at = timezone.now()
    StageTransition.objects.create(
        application=application,
        from_stage=previous_stage,
        to_stage=target_stage,
    )
    application.stage = target_stage
    application.save(update_fields=["stage", "first_submitted_at", "updated_at"])
    return application


@transaction.atomic
def create_recruitment_event(
    *,
    account: Account,
    application_id: int,
    event_type: str,
    scheduled_at: datetime,
    custom_title: str = "",
) -> RecruitmentEvent:
    application = JobApplication.objects.get(pk=application_id, account=account)
    event = RecruitmentEvent(
        application=application,
        event_type=event_type,
        custom_title=custom_title,
        scheduled_at=scheduled_at,
    )
    event.full_clean()
    event.save()
    return event


@transaction.atomic
def update_recruitment_event(
    *,
    account: Account,
    application_id: int,
    event_id: int,
    event_type: str,
    scheduled_at: datetime,
    status: str,
    custom_title: str = "",
) -> RecruitmentEvent:
    event = RecruitmentEvent.objects.select_for_update().get(
        pk=event_id,
        application_id=application_id,
        application__account=account,
        status=RecruitmentEvent.Status.SCHEDULED,
    )
    event.event_type = event_type
    event.custom_title = custom_title
    event.scheduled_at = scheduled_at
    event.status = status
    event.full_clean()
    event.save(update_fields=["event_type", "custom_title", "scheduled_at", "status", "updated_at"])
    return event
