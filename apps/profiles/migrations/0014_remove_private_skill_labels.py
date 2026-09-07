from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0013_candidateprofile_contact_email"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="profileskill",
            name="profile_skill_label_not_blank",
        ),
        migrations.RemoveConstraint(
            model_name="experienceskill",
            name="experience_skill_label_not_blank",
        ),
        migrations.RemoveConstraint(
            model_name="projectskill",
            name="project_skill_label_not_blank",
        ),
        migrations.RemoveField(
            model_name="profileskill",
            name="label",
        ),
        migrations.RemoveField(
            model_name="experienceskill",
            name="label",
        ),
        migrations.RemoveField(
            model_name="projectskill",
            name="label",
        ),
    ]
