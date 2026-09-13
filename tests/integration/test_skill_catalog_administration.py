from datetime import date

import pytest
from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.applications.models import ApplicationSkillRequirement, JobApplication
from apps.applications.services import create_or_reuse_company
from apps.campaigns.models import Campaign
from apps.profiles.models import (
    CandidateProfile,
    Experience,
    ExperienceSkill,
    ProfileSkill,
    Project,
    ProjectSkill,
)
from apps.skills.models import SkillAlias, SkillCatalogAudit, SkillConcept
from apps.skills.services import (
    StaleCatalogPreviewError,
    create_skill_alias,
    delete_skill_alias,
    preview_skill_alias_reassignment,
    reassign_skill_alias,
    resolve_skill_label,
    skill_catalog_issues,
    skill_concept_detail,
)

pytestmark = pytest.mark.integration

REQUIRED = ApplicationSkillRequirement.Classification.REQUIRED
PREFERRED = ApplicationSkillRequirement.Classification.PREFERRED


def administrator() -> Account:
    return Account.objects.create_superuser("admin@example.com", "a-secure-password")


def candidate(email: str = "candidate@example.com") -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    CandidateProfile.objects.create(
        account=account,
        full_name="Ada Lovelace",
        timezone="Europe/London",
    )
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    return account


def application(account: Account, company_name: str = "Example Careers") -> JobApplication:
    company, _ = create_or_reuse_company(company_name)
    return JobApplication.objects.create(
        account=account,
        campaign=Campaign.objects.get(account=account),
        company=company,
        role_title="Platform engineer",
        job_description="Build dependable internal systems.",
    )


def canonical_alias(concept: SkillConcept) -> SkillAlias:
    return SkillAlias.objects.get(concept=concept, is_canonical=True)


def requirement(
    application_: JobApplication,
    alias: SkillAlias,
    classification: str,
) -> ApplicationSkillRequirement:
    return ApplicationSkillRequirement.objects.create(
        application=application_,
        alias=alias,
        classification=classification,
    )


@pytest.mark.django_db
def test_concept_detail_reports_aliases_and_public_and_private_reference_counts() -> None:
    concept, _ = resolve_skill_label("Python")
    create_skill_alias(concept=concept, display_name="py")
    account = candidate()
    application_ = application(account)
    requirement(application_, canonical_alias(concept), REQUIRED)
    profile = CandidateProfile.objects.get(account=account)
    ProfileSkill.objects.create(profile=profile, concept=concept)
    experience = Experience.objects.create(
        profile=profile,
        role="Engineer",
        organization="Acme",
        location="Remote",
        start_date=date(2020, 1, 1),
    )
    ExperienceSkill.objects.create(experience=experience, concept=concept)
    project = Project.objects.create(profile=profile, name="Side project")
    ProjectSkill.objects.create(project=project, concept=concept)

    detail = skill_concept_detail(concept=concept)

    assert detail.concept_id == concept.pk
    assert detail.canonical_name == "Python"
    assert detail.public_alias_count == 2
    assert detail.profile_skill_count == 1
    assert detail.experience_skill_count == 1
    assert detail.project_skill_count == 1
    assert detail.application_requirement_count == 1
    aliases_by_name = {alias.display_name: alias for alias in detail.aliases}
    assert aliases_by_name["Python"].application_requirement_count == 1
    assert aliases_by_name["Python"].is_canonical is True
    assert aliases_by_name["py"].application_requirement_count == 0
    assert aliases_by_name["py"].is_canonical is False


@pytest.mark.django_db
def test_concept_detail_never_exposes_private_application_content() -> None:
    concept, _ = resolve_skill_label("Python")
    account = candidate()
    application_ = application(account)
    requirement(application_, canonical_alias(concept), REQUIRED)

    detail = skill_concept_detail(concept=concept)

    serialized = repr(detail)
    assert "Platform engineer" not in serialized
    assert "Build dependable internal systems." not in serialized
    assert "Example Careers" not in serialized


@pytest.mark.django_db
def test_preview_requires_an_administrator() -> None:
    account = candidate()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]

    with pytest.raises(ValidationError):
        preview_skill_alias_reassignment(
            alias=alias,
            destination_concept=destination,
            actor=account,
        )


@pytest.mark.django_db
def test_reassignment_requires_an_administrator() -> None:
    account = candidate()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator(),
    )

    with pytest.raises(ValidationError):
        reassign_skill_alias(
            alias=alias,
            destination_concept=destination,
            actor=account,
            reason="Repair",
            preview_token=preview.token,
        )


@pytest.mark.django_db
def test_deletion_requires_an_administrator() -> None:
    account = candidate()
    source, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=source, display_name="py")[0]

    with pytest.raises(ValidationError):
        delete_skill_alias(alias=alias, actor=account, reason="Repair")

    assert SkillAlias.objects.filter(pk=alias.pk).exists()


@pytest.mark.django_db
def test_reassignment_requires_a_repair_reason() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    with pytest.raises(ValidationError):
        reassign_skill_alias(
            alias=alias,
            destination_concept=destination,
            actor=administrator_,
            reason="   ",
            preview_token=preview.token,
        )

    alias.refresh_from_db()
    assert alias.concept_id == source.pk


@pytest.mark.django_db
def test_deletion_requires_a_repair_reason() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=source, display_name="py")[0]

    with pytest.raises(ValidationError):
        delete_skill_alias(alias=alias, actor=administrator_, reason="  ")

    assert SkillAlias.objects.filter(pk=alias.pk).exists()


@pytest.mark.django_db
def test_preview_lists_affected_applications_without_mutating_the_catalog() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    first = application(account, "First Company")
    second = application(account, "Second Company")
    first_requirement = requirement(first, alias, REQUIRED)
    second_requirement = requirement(second, alias, PREFERRED)

    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    assert set(preview.affected_application_ids) == {first.pk, second.pk}
    assert set(preview.affected_requirement_ids) == {
        first_requirement.pk,
        second_requirement.pk,
    }
    assert preview.collisions == ()
    assert preview.token

    alias.refresh_from_db()
    assert alias.concept_id == source.pk


@pytest.mark.django_db
def test_confirmed_reassignment_moves_every_requirement_to_the_destination_concept() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    first = application(account, "First Company")
    second = application(account, "Second Company")
    first_requirement = requirement(first, alias, REQUIRED)
    second_requirement = requirement(second, alias, PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    result = reassign_skill_alias(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
        reason="Repair the alias identity.",
        preview_token=preview.token,
    )

    alias.refresh_from_db()
    assert alias.concept_id == destination.pk
    assert alias.display_name == "py"
    for row in (first_requirement, second_requirement):
        row.refresh_from_db()
        assert row.alias_id == alias.pk
        assert row.alias.concept_id == destination.pk
    assert first_requirement.classification == REQUIRED
    assert second_requirement.classification == PREFERRED
    assert result.audit.operation == SkillCatalogAudit.Operation.ALIAS_REASSIGNMENT
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_collision_keeps_the_destination_row_and_promotes_required() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    source_requirement = requirement(application_, alias, REQUIRED)
    destination_requirement = requirement(application_, canonical_alias(destination), PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    assert len(preview.collisions) == 1
    collision = preview.collisions[0]
    assert collision.application_id == application_.pk
    assert collision.kept_requirement_id == destination_requirement.pk
    assert collision.removed_requirement_id == source_requirement.pk
    assert collision.kept_classification == REQUIRED
    assert collision.promoted is True

    result = reassign_skill_alias(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
        reason="Collapse a duplicate requirement.",
        preview_token=preview.token,
    )

    assert not ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()
    destination_requirement.refresh_from_db()
    assert destination_requirement.classification == REQUIRED
    assert result.audit.removed_requirement_ids == [source_requirement.pk]
    assert result.audit.promoted_requirement_ids == [destination_requirement.pk]
    assert result.audit.collision_application_ids == [application_.pk]
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_collision_without_a_required_row_keeps_the_preferred_classification() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    source_requirement = requirement(application_, alias, PREFERRED)
    destination_requirement = requirement(application_, canonical_alias(destination), PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    assert preview.collisions[0].promoted is False

    result = reassign_skill_alias(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
        reason="Collapse a duplicate requirement.",
        preview_token=preview.token,
    )

    assert not ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()
    destination_requirement.refresh_from_db()
    assert destination_requirement.classification == PREFERRED
    assert result.audit.kept_requirement_ids == [destination_requirement.pk]
    assert result.audit.promoted_requirement_ids == []
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_reassignment_rejects_a_canonical_alias() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    canonical = canonical_alias(source)

    with pytest.raises(ValidationError):
        preview_skill_alias_reassignment(
            alias=canonical,
            destination_concept=destination,
            actor=administrator_,
        )


@pytest.mark.django_db
def test_reassignment_rejects_the_current_concept() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=source, display_name="py")[0]

    with pytest.raises(ValidationError):
        preview_skill_alias_reassignment(
            alias=alias,
            destination_concept=source,
            actor=administrator_,
        )


@pytest.mark.django_db
def test_a_stale_preview_cannot_be_confirmed_and_changes_nothing() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    source_requirement = requirement(application_, alias, PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )
    # A concurrent edit changes a requirement the previewed plan depends on.
    ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).update(
        classification=REQUIRED
    )

    with pytest.raises(StaleCatalogPreviewError):
        reassign_skill_alias(
            alias=alias,
            destination_concept=destination,
            actor=administrator_,
            reason="Repair",
            preview_token=preview.token,
        )

    alias.refresh_from_db()
    assert alias.concept_id == source.pk
    source_requirement.refresh_from_db()
    assert source_requirement.classification == REQUIRED
    assert ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()


@pytest.mark.django_db
def test_an_unrelated_requirement_change_does_not_stale_the_preview() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    unrelated, _ = resolve_skill_label("Rust")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    source_requirement = requirement(application_, alias, PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )
    unrelated_requirement = requirement(application_, canonical_alias(unrelated), REQUIRED)

    result = reassign_skill_alias(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
        reason="Repair",
        preview_token=preview.token,
    )

    alias.refresh_from_db()
    assert alias.concept_id == destination.pk
    assert result.audit.affected_requirement_ids == [source_requirement.pk]
    assert unrelated_requirement.pk not in result.audit.affected_requirement_ids
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_a_reassignment_without_a_preview_token_is_rejected() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]

    with pytest.raises(ValidationError):
        reassign_skill_alias(
            alias=alias,
            destination_concept=destination,
            actor=administrator_,
            reason="Repair",
            preview_token="",
        )


@pytest.mark.django_db
def test_failed_reassignment_persistence_rolls_back_the_alias_and_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    source_requirement = requirement(application_, alias, REQUIRED)
    destination_requirement = requirement(application_, canonical_alias(destination), PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Audit persistence failed")

    monkeypatch.setattr(SkillCatalogAudit.objects, "create", fail_audit)

    with pytest.raises(RuntimeError):
        reassign_skill_alias(
            alias=alias,
            destination_concept=destination,
            actor=administrator_,
            reason="Repair",
            preview_token=preview.token,
        )

    alias.refresh_from_db()
    assert alias.concept_id == source.pk
    assert ApplicationSkillRequirement.objects.filter(pk=source_requirement.pk).exists()
    destination_requirement.refresh_from_db()
    assert destination_requirement.classification == PREFERRED
    assert SkillCatalogAudit.objects.count() == 0


@pytest.mark.django_db
def test_referenced_alias_cannot_be_deleted() -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=concept, display_name="py")[0]
    account = candidate()
    requirement(application(account), alias, REQUIRED)

    with pytest.raises(ValidationError):
        delete_skill_alias(alias=alias, actor=administrator_, reason="Repair")

    assert SkillAlias.objects.filter(pk=alias.pk).exists()


@pytest.mark.django_db
def test_canonical_alias_cannot_be_deleted() -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    canonical = canonical_alias(concept)

    with pytest.raises(ValidationError):
        delete_skill_alias(alias=canonical, actor=administrator_, reason="Repair")

    assert SkillAlias.objects.filter(pk=canonical.pk).exists()


@pytest.mark.django_db
def test_unreferenced_noncanonical_alias_is_deleted_through_the_audited_operation() -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=concept, display_name="py")[0]

    audit = delete_skill_alias(
        alias=alias,
        actor=administrator_,
        reason="Remove duplicate wording.",
    )

    assert not SkillAlias.objects.filter(pk=alias.pk).exists()
    assert audit.operation == SkillCatalogAudit.Operation.ALIAS_DELETION
    assert audit.actor_id == administrator_.pk
    assert audit.reason == "Remove duplicate wording."
    assert audit.alias_id == alias.pk
    assert audit.alias_display_name == "py"
    assert audit.source_concept_id == concept.pk
    assert audit.destination_concept_id is None
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_failed_deletion_persistence_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=concept, display_name="py")[0]

    def fail_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Audit persistence failed")

    monkeypatch.setattr(SkillCatalogAudit.objects, "create", fail_audit)

    with pytest.raises(RuntimeError):
        delete_skill_alias(alias=alias, actor=administrator_, reason="Repair")

    assert SkillAlias.objects.filter(pk=alias.pk).exists()
    assert SkillCatalogAudit.objects.count() == 0
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_reassignment_audit_records_actor_reason_identity_and_collision_outcomes() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    source_requirement = requirement(application_, alias, REQUIRED)
    destination_requirement = requirement(application_, canonical_alias(destination), PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
    )

    result = reassign_skill_alias(
        alias=alias,
        destination_concept=destination,
        actor=administrator_,
        reason="Reassign wording to its real concept.",
        preview_token=preview.token,
    )

    audit = result.audit
    assert audit.actor_id == administrator_.pk
    assert audit.actor_email == administrator_.email
    assert audit.reason == "Reassign wording to its real concept."
    assert audit.alias_id == alias.pk
    assert audit.alias_display_name == "py"
    assert audit.alias_normalized_value == "py"
    assert audit.source_concept_id == source.pk
    assert audit.source_concept_name == "Python"
    assert audit.source_concept_key == "python"
    assert audit.destination_concept_id == destination.pk
    assert audit.destination_concept_name == "Django"
    assert audit.destination_concept_key == "django"
    assert set(audit.affected_application_ids) == {application_.pk}
    assert set(audit.affected_requirement_ids) == {
        source_requirement.pk,
        destination_requirement.pk,
    }
    assert audit.removed_requirement_ids == [source_requirement.pk]
    assert audit.promoted_requirement_ids == [destination_requirement.pk]
    assert audit.kept_requirement_ids == [destination_requirement.pk]
    assert audit.collision_application_ids == [application_.pk]
    assert audit.affected_requirement_count == 2


@pytest.mark.django_db
def test_catalog_audits_are_immutable() -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=concept, display_name="py")[0]
    audit = delete_skill_alias(alias=alias, actor=administrator_, reason="Repair")

    audit.reason = "Changed"
    with pytest.raises(ValidationError):
        audit.save()
    with pytest.raises(ValidationError):
        audit.delete()

    audit.refresh_from_db()
    assert audit.reason == "Repair"


@pytest.mark.django_db
def test_catalog_stays_healthy_after_a_full_reassignment_and_deletion() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    moved = create_skill_alias(concept=source, display_name="py")[0]
    removable = create_skill_alias(concept=source, display_name="python3")[0]
    account = candidate()
    application_ = application(account)
    requirement(application_, moved, REQUIRED)
    requirement(application_, canonical_alias(destination), PREFERRED)
    preview = preview_skill_alias_reassignment(
        alias=moved,
        destination_concept=destination,
        actor=administrator_,
    )

    reassign_skill_alias(
        alias=moved,
        destination_concept=destination,
        actor=administrator_,
        reason="Reassign",
        preview_token=preview.token,
    )
    delete_skill_alias(alias=removable, actor=administrator_, reason="Remove")

    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_administrator_sees_alias_and_reference_counts_on_the_concept_page() -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    create_skill_alias(concept=concept, display_name="py")
    account = candidate()
    requirement(application(account), canonical_alias(concept), REQUIRED)
    profile = CandidateProfile.objects.get(account=account)
    ProfileSkill.objects.create(profile=profile, concept=concept)
    client = Client()
    client.force_login(administrator_)

    response = client.get(reverse("admin:skills_skillconcept_change", args=[concept.pk]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "py" in content
    assert "Application skill requirements" in content
    assert "Private references" in content
    assert "Profile candidate skill associations" in content
    assert "Example Careers" not in content
    assert "Platform engineer" not in content


@pytest.mark.django_db
def test_administrator_previews_and_confirms_alias_reassignment_through_the_admin() -> None:
    administrator_ = administrator()
    source, _ = resolve_skill_label("Python")
    destination, _ = resolve_skill_label("Django")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    account = candidate()
    application_ = application(account)
    requirement(application_, alias, REQUIRED)
    client = Client()
    client.force_login(administrator_)
    url = reverse("admin:skills_skillalias_reassign", args=[alias.pk])

    preview_response = client.post(
        url,
        {"destination": str(destination.pk), "reason": "Repair", "action": "preview"},
    )

    assert preview_response.status_code == 200
    assert application_.pk in preview_response.context["preview"].affected_application_ids
    token = preview_response.context["preview"].token
    alias.refresh_from_db()
    assert alias.concept_id == source.pk

    confirm_response = client.post(
        url,
        {
            "destination": str(destination.pk),
            "reason": "Repair",
            "action": "confirm",
            "preview_token": token,
        },
    )

    assert confirm_response.status_code == 302
    alias.refresh_from_db()
    assert alias.concept_id == destination.pk
    assert (
        SkillCatalogAudit.objects.filter(
            operation=SkillCatalogAudit.Operation.ALIAS_REASSIGNMENT
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_administrator_deletes_an_eligible_alias_through_the_admin() -> None:
    administrator_ = administrator()
    concept, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=concept, display_name="py")[0]
    client = Client()
    client.force_login(administrator_)

    response = client.post(
        reverse("admin:skills_skillalias_delete_audited", args=[alias.pk]),
        {"reason": "Remove duplicate wording."},
    )

    assert response.status_code == 302
    assert not SkillAlias.objects.filter(pk=alias.pk).exists()
    assert (
        SkillCatalogAudit.objects.filter(
            operation=SkillCatalogAudit.Operation.ALIAS_DELETION
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_candidate_cannot_reach_alias_reassignment_administration() -> None:
    account = candidate()
    source, _ = resolve_skill_label("Python")
    alias = create_skill_alias(concept=source, display_name="py")[0]
    client = Client()
    client.force_login(account)

    response = client.get(reverse("admin:skills_skillalias_reassign", args=[alias.pk]))

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")
    alias.refresh_from_db()
    assert alias.concept_id == source.pk
