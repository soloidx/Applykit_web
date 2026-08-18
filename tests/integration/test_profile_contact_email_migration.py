import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.integration


@pytest.mark.django_db(transaction=True)
def test_profile_contact_email_migration_backfills_existing_profiles() -> None:
    executor = MigrationExecutor(connection)
    old_migration = [("profiles", "0012_remove_project_technologies")]
    new_migration = [("profiles", "0013_candidateprofile_contact_email")]
    executor.migrate(old_migration)

    old_apps = executor.loader.project_state(old_migration).apps
    Account = old_apps.get_model("accounts", "Account")
    CandidateProfile = old_apps.get_model("profiles", "CandidateProfile")
    account = Account.objects.create(email="historical@example.com")
    profile = CandidateProfile.objects.create(
        account=account,
        full_name="Historical Candidate",
        timezone="UTC",
    )

    try:
        executor = MigrationExecutor(connection)
        executor.migrate(new_migration)
        new_apps = executor.loader.project_state(new_migration).apps
        CandidateProfile = new_apps.get_model("profiles", "CandidateProfile")

        profile = CandidateProfile.objects.get(pk=profile.pk)
        assert profile.contact_email == "historical@example.com"
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(new_migration)
