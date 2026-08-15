import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.integration


@pytest.mark.django_db(transaction=True)
def test_profile_skill_migration_preserves_private_rows_and_reuses_catalog_concepts() -> None:
    executor = MigrationExecutor(connection)
    latest = [("profiles", "0012_remove_project_technologies")]
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
        executor.migrate(latest)
        new_apps = executor.loader.project_state(latest).apps
        ProfileSkill = new_apps.get_model("profiles", "ProfileSkill")
        SkillConcept = new_apps.get_model("skills", "SkillConcept")
        SkillAlias = new_apps.get_model("skills", "SkillAlias")
        Project = new_apps.get_model("profiles", "Project")

        migrated = list(ProfileSkill.objects.order_by("profile_id", "position"))
        assert [(skill.label, skill.position) for skill in migrated] == [
            ("Python", 3),
            ("Ｐython", 4),
            ("PYTHON", 1),
        ]
        assert {skill.profile_id for skill in migrated} == {first_profile.pk, second_profile.pk}
        assert all(skill.concept_id is not None for skill in migrated)
        python_concept_id = SkillConcept.objects.get(canonical_key="python").pk
        legacy_collision_concept_id = SkillConcept.objects.get(canonical_key="ｐython").pk
        assert (
            ProfileSkill.objects.filter(
                profile_id=first_profile.pk,
                concept_id=python_concept_id,
            ).count()
            == 1
        )
        assert (
            ProfileSkill.objects.filter(
                profile_id=first_profile.pk,
                concept_id=legacy_collision_concept_id,
            ).count()
            == 1
        )
        assert (
            ProfileSkill.objects.filter(
                profile_id=second_profile.pk,
                concept_id=python_concept_id,
            ).count()
            == 1
        )
        assert SkillConcept.objects.filter(canonical_key__in=["python", "ｐython"]).count() == 2
        assert SkillAlias.objects.filter(normalized_value__in=["python", "ｐython"]).count() == 2
        assert Project.objects.filter(pk=legacy_project.pk).exists()
        assert "technologies" not in {field.name for field in Project._meta.fields}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
