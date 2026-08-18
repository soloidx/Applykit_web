from typing import Any

from django.db import migrations, models


def populate_project_relevance(apps: Any, schema_editor: Any) -> None:
    ResumeProject = apps.get_model("resumes", "ResumeProject")
    ApplicationSkillRequirement = apps.get_model("applications", "ApplicationSkillRequirement")
    ProjectSkill = apps.get_model("profiles", "ProjectSkill")

    for overlay in ResumeProject.objects.select_related("resume__application", "project"):
        requirements = set(
            ApplicationSkillRequirement.objects.filter(
                application_id=overlay.resume.application_id,
            ).values_list("concept_id", flat=True)
        )
        overlay.is_relevant = ProjectSkill.objects.filter(
            project_id=overlay.project_id,
            concept_id__in=requirements,
        ).exists()
        overlay.save(update_fields=["is_relevant"])


class Migration(migrations.Migration):
    dependencies = [("resumes", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="resumeproject",
            name="is_relevant",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(populate_project_relevance, migrations.RunPython.noop),
    ]
