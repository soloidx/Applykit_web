import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0009_profile_skill_contract"),
        ("skills", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExperienceSkill",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("label", models.CharField(max_length=200)),
                ("normalized_label", models.CharField(editable=False, max_length=200)),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "concept",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="experience_skills",
                        to="skills.skillconcept",
                    ),
                ),
                (
                    "experience",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="experience_skills",
                        to="profiles.experience",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("experience", "concept"),
                        name="experience_skill_unique_concept",
                    ),
                    models.CheckConstraint(
                        condition=~Q(normalized_label=""),
                        name="experience_skill_label_not_blank",
                    ),
                ],
            },
        ),
    ]
