from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.core.checks import Tags, run_checks
from django.core.exceptions import ValidationError
from django.db import connection

from apps.accounts.models import Account
from apps.applications.models import ApplicationSkillRequirement, Company, JobApplication
from apps.campaigns.models import Campaign
from apps.profiles.models import CandidateProfile
from apps.skills.models import SkillAlias, SkillConcept
from apps.skills.services import delete_skill_alias, resolve_skill_label, skill_catalog_issues

pytestmark = pytest.mark.integration


def _application(email: str) -> JobApplication:
    account = Account.objects.create_user(email, "a-secure-password")
    CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )
    campaign = Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    company = Company.objects.create(name="Example Careers")
    return JobApplication.objects.create(
        account=account,
        campaign=campaign,
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )


@contextmanager
def unguarded_foreign_keys() -> Iterator[None]:
    """Temporarily bypass foreign-key enforcement to simulate catalog corruption."""

    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA foreign_keys = ON")
    elif connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET session_replication_role = replica")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET session_replication_role = origin")
    else:  # pragma: no cover - no other backend is configured
        yield


@pytest.mark.django_db
def test_catalog_health_detects_duplicate_effective_concepts_per_application() -> None:
    application = _application("duplicate@example.com")
    concept, _ = resolve_skill_label("Python")
    canonical = SkillAlias.objects.get(concept=concept, is_canonical=True)
    variant = SkillAlias.objects.create(concept=concept, display_name="py")
    ApplicationSkillRequirement.objects.create(
        application=application,
        alias=canonical,
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )
    ApplicationSkillRequirement.objects.create(
        application=application,
        alias=variant,
        classification=ApplicationSkillRequirement.Classification.PREFERRED,
    )

    issues = skill_catalog_issues()

    assert len(issues) == 1
    assert "skill concept" in issues[0]


@pytest.mark.django_db(transaction=True)
def test_catalog_health_detects_a_dangling_application_requirement_reference() -> None:
    application = _application("dangling@example.com")
    concept, _ = resolve_skill_label("Python")
    alias = SkillAlias.objects.get(concept=concept, is_canonical=True)
    requirement = ApplicationSkillRequirement.objects.create(
        application=application,
        alias=alias,
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )

    with unguarded_foreign_keys():
        ApplicationSkillRequirement.objects.filter(pk=requirement.pk).update(alias_id=999_999)

    issues = skill_catalog_issues()

    assert len(issues) == 1
    assert "missing catalog record" in issues[0]


@pytest.mark.django_db(transaction=True)
def test_catalog_health_detects_a_dangling_skill_alias() -> None:
    concept, _ = resolve_skill_label("Python")
    alias = SkillAlias.objects.create(concept=concept, display_name="py")

    with unguarded_foreign_keys():
        SkillAlias.objects.filter(pk=alias.pk).update(concept_id=999_999)

    issues = skill_catalog_issues()

    assert any("missing catalog record" in issue for issue in issues)


@pytest.mark.django_db
def test_canonical_alias_cannot_be_deleted_through_the_domain_operation() -> None:
    concept, _ = resolve_skill_label("Python")
    canonical = SkillAlias.objects.get(concept=concept, is_canonical=True)

    with pytest.raises(ValidationError):
        delete_skill_alias(alias=canonical)

    assert SkillAlias.objects.filter(pk=canonical.pk).exists()


@pytest.mark.django_db
def test_referenced_alias_cannot_be_deleted_through_the_domain_operation() -> None:
    application = _application("referenced@example.com")
    concept, _ = resolve_skill_label("Python")
    variant = SkillAlias.objects.create(concept=concept, display_name="py")
    ApplicationSkillRequirement.objects.create(
        application=application,
        alias=variant,
        classification=ApplicationSkillRequirement.Classification.REQUIRED,
    )

    with pytest.raises(ValidationError):
        delete_skill_alias(alias=variant)

    assert SkillAlias.objects.filter(pk=variant.pk).exists()


@pytest.mark.django_db
def test_unreferenced_noncanonical_alias_can_be_deleted_through_the_domain_operation() -> None:
    concept, _ = resolve_skill_label("Python")
    variant = SkillAlias.objects.create(concept=concept, display_name="py")

    delete_skill_alias(alias=variant)

    assert not SkillAlias.objects.filter(pk=variant.pk).exists()
    assert SkillAlias.objects.filter(concept=concept, is_canonical=True).exists()
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_direct_alias_and_concept_deletion_is_rejected() -> None:
    concept, _ = resolve_skill_label("Python")
    variant = SkillAlias.objects.create(concept=concept, display_name="py")

    with pytest.raises(ValidationError):
        variant.delete()
    with pytest.raises(ValidationError):
        concept.delete()

    assert SkillAlias.objects.filter(pk=variant.pk).exists()
    assert SkillConcept.objects.filter(pk=concept.pk).exists()


@pytest.mark.django_db
def test_database_system_check_reports_catalog_drift() -> None:
    concept, _ = resolve_skill_label("Python")
    SkillAlias.objects.filter(concept=concept, is_canonical=True).delete()

    errors = run_checks(tags=[Tags.database], databases=["default"])

    assert any(error.id == "skills.E001" for error in errors)


@pytest.mark.django_db
def test_database_system_check_passes_for_a_healthy_catalog() -> None:
    resolve_skill_label("Python")

    errors = run_checks(tags=[Tags.database], databases=["default"])
    skill_errors = [error for error in errors if error.id == "skills.E001"]

    assert skill_errors == []
