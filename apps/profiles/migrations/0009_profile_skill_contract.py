import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0008_profile_skill_backfill"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="profileskill",
            name="skill_unique_name_per_profile",
        ),
        migrations.AlterField(
            model_name="profileskill",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="profile_skills",
                to="profiles.candidateprofile",
            ),
        ),
        migrations.RemoveField(
            model_name="profileskill",
            name="name",
        ),
        migrations.RemoveField(
            model_name="profileskill",
            name="normalized_name",
        ),
        migrations.AlterField(
            model_name="profileskill",
            name="concept",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="profile_skills",
                to="skills.skillconcept",
            ),
        ),
        migrations.AlterField(
            model_name="profileskill",
            name="label",
            field=models.CharField(max_length=200),
        ),
        migrations.AlterField(
            model_name="profileskill",
            name="normalized_label",
            field=models.CharField(editable=False, max_length=200),
        ),
        migrations.AddConstraint(
            model_name="profileskill",
            constraint=models.UniqueConstraint(
                fields=("profile", "concept"),
                name="profile_skill_unique_concept",
            ),
        ),
        migrations.AddConstraint(
            model_name="profileskill",
            constraint=models.CheckConstraint(
                condition=~Q(normalized_label=""),
                name="profile_skill_label_not_blank",
            ),
        ),
    ]
