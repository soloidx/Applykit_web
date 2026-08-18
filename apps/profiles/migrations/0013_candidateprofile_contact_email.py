from typing import Any

from django.db import migrations, models


def backfill_contact_email(apps: Any, schema_editor: Any) -> None:
    CandidateProfile = apps.get_model("profiles", "CandidateProfile")
    for profile in CandidateProfile.objects.select_related("account").iterator():
        profile.contact_email = profile.account.email
        profile.save(update_fields=["contact_email"])


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0012_remove_project_technologies"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidateprofile",
            name="contact_email",
            field=models.EmailField(max_length=254, null=True),
        ),
        migrations.RunPython(backfill_contact_email, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="candidateprofile",
            name="contact_email",
            field=models.EmailField(max_length=254),
        ),
    ]
