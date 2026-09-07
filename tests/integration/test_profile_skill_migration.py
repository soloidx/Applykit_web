import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.integration


@pytest.mark.django_db(transaction=True)
def test_profile_skill_migration_preserves_private_rows_and_reuses_catalog_concepts() -> None:
    executor = MigrationExecutor(connection)
    legacy_head = [("profiles", "0013_candidateprofile_contact_email")]
    current_head = [("profiles", "0014_remove_private_skill_labels")]
    executor.migrate([("profiles", "0006_projectskill")])

    old_apps = executor.loader.project_state([("profiles", "0006_projectskill")]).apps
    Account = old_apps.get_model("accounts", "Account")
    CandidateProfile = old_apps.get_model("profiles", "CandidateProfile")
    Skill = old_apps.get_model("profiles", "Skill")
    Project = old_apps.get_model("profiles", "Project")
    first_account = Account.objects.create(email="first@example.com")
    second_account = Account.objects.create(email="second@example.com")
    first_profile = CandidateProfile.objects.create(
        account=first_account,
        full_name="First Candidate",
        timezone="UTC",
    )
    second_profile = CandidateProfile.objects.create(
        account=second_account,
        full_name="Second Candidate",
        timezone="UTC",
    )
    legacy_project = Project.objects.create(
        profile=first_profile,
        name="Legacy project",
        technologies="Python, Django",
    )
    Skill.objects.create(
        profile=first_profile,
        name=" Python ",
        normalized_name="python",
        position=3,
    )
    Skill.objects.create(
        profile=first_profile,
        name="Ｐython",
        normalized_name="ｐython",
        position=4,
    )
    Skill.objects.create(
        profile=second_profile,
        name="PYTHON",
        normalized_name="python",
        position=1,
    )

    try:
        executor = MigrationExecutor(connection)
        executor.migrate(legacy_head)
        legacy_apps = executor.loader.project_state(legacy_head).apps
        LegacyProfileSkill = legacy_apps.get_model("profiles", "ProfileSkill")
        LegacyCandidateProfile = legacy_apps.get_model("profiles", "CandidateProfile")
        SkillConcept = legacy_apps.get_model("skills", "SkillConcept")
        SkillAlias = legacy_apps.get_model("skills", "SkillAlias")
        LegacyProject = legacy_apps.get_model("profiles", "Project")

        migrated = list(LegacyProfileSkill.objects.order_by("profile_id", "position"))
        assert [(skill.label, skill.position) for skill in migrated] == [
            ("Python", 3),
            ("Ｐython", 4),
            ("PYTHON", 1),
        ]
        assert {skill.profile_id for skill in migrated} == {first_profile.pk, second_profile.pk}
        assert (
            LegacyCandidateProfile.objects.get(pk=first_profile.pk).contact_email
            == first_account.email
        )
        assert (
            LegacyCandidateProfile.objects.get(pk=second_profile.pk).contact_email
            == second_account.email
        )
        assert all(skill.concept_id is not None for skill in migrated)
        python_concept_id = SkillConcept.objects.get(canonical_key="python").pk
        legacy_collision_concept_id = SkillConcept.objects.get(canonical_key="ｐython").pk
        assert (
            LegacyProfileSkill.objects.filter(
                profile_id=first_profile.pk,
                concept_id=python_concept_id,
            ).count()
            == 1
        )
        assert (
            LegacyProfileSkill.objects.filter(
                profile_id=first_profile.pk,
                concept_id=legacy_collision_concept_id,
            ).count()
            == 1
        )
        assert (
            LegacyProfileSkill.objects.filter(
                profile_id=second_profile.pk,
                concept_id=python_concept_id,
            ).count()
            == 1
        )
        assert SkillConcept.objects.filter(canonical_key__in=["python", "ｐython"]).count() == 2
        assert SkillAlias.objects.filter(normalized_value__in=["python", "ｐython"]).count() == 2
        assert LegacyProject.objects.filter(pk=legacy_project.pk).exists()
        assert "technologies" not in {field.name for field in LegacyProject._meta.fields}

        executor = MigrationExecutor(connection)
        executor.migrate(current_head)
        catalog_apps = executor.loader.project_state(current_head).apps
        ProfileSkill = catalog_apps.get_model("profiles", "ProfileSkill")
        CatalogConcept = catalog_apps.get_model("skills", "SkillConcept")
        assert "label" not in {field.name for field in ProfileSkill._meta.fields}
        rows = list(ProfileSkill.objects.order_by("profile_id", "position"))
        assert [(row.position, row.concept_id) for row in rows] == [
            (3, python_concept_id),
            (4, legacy_collision_concept_id),
            (1, python_concept_id),
        ]
        assert CatalogConcept.objects.filter(canonical_key__in=["python", "ｐython"]).count() == 2
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(current_head)
