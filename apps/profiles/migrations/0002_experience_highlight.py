from django.db import migrations, models
from django.db.models import F, Q
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Experience",
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
                ("role", models.CharField(max_length=200)),
                ("organization", models.CharField(max_length=200)),
                ("location", models.CharField(blank=True, max_length=200)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, null=True)),
                ("description", models.TextField(blank=True)),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="experiences",
                        to="profiles.candidateprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                        name="experience_end_date_on_or_after_start",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Highlight",
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
                ("text", models.TextField()),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "experience",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="highlights",
                        to="profiles.experience",
                    ),
                ),
            ],
            options={"ordering": ["position", "id"]},
        ),
    ]
