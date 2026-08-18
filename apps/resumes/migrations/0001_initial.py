import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        (
            "applications",
            "0006_remove_applicationskillrequirement_application_skill_requirement_label_not_blank_and_more",
        ),
        ("profiles", "0013_candidateprofile_contact_email"),
        ("skills", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Resume",
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
                (
                    "contact_email_override",
                    models.EmailField(blank=True, max_length=254, null=True),
                ),
                ("full_name_override", models.CharField(blank=True, max_length=200, null=True)),
                (
                    "professional_title_override",
                    models.CharField(blank=True, max_length=200, null=True),
                ),
                ("professional_summary_override", models.TextField(blank=True, null=True)),
                ("phone_number_override", models.CharField(blank=True, max_length=32, null=True)),
                ("location_override", models.CharField(blank=True, max_length=200, null=True)),
                ("linkedin_url_override", models.URLField(blank=True, null=True)),
                ("portfolio_url_override", models.URLField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "application",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resume",
                        to="applications.jobapplication",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ResumeSection",
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
                ("kind", models.CharField(choices=[("summary", "Summary"), ("skills", "Skills"), ("experience", "Experience"), ("projects", "Projects"), ("education", "Education"), ("languages", "Languages")], max_length=16)),
                ("position", models.PositiveIntegerField(default=0)),
                (
                    "resume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sections",
                        to="resumes.resume",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume", "kind"), name="resume_section_unique_kind"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ResumeExperience",
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
                ("included", models.BooleanField(default=True)),
                ("is_relevant", models.BooleanField(default=False)),
                ("position", models.PositiveIntegerField(default=0)),
                ("role_override", models.CharField(blank=True, max_length=200, null=True)),
                (
                    "organization_override",
                    models.CharField(blank=True, max_length=200, null=True),
                ),
                ("location_override", models.CharField(blank=True, max_length=200, null=True)),
                ("start_date_override", models.DateField(blank=True, null=True)),
                ("end_date_override", models.DateField(blank=True, null=True)),
                ("description_override", models.TextField(blank=True, null=True)),
                (
                    "experience",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resume_experiences",
                        to="profiles.experience",
                    ),
                ),
                (
                    "resume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="experiences",
                        to="resumes.resume",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume", "experience"), name="resume_experience_unique_source"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ResumeEducation",
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
                ("included", models.BooleanField(default=True)),
                ("position", models.PositiveIntegerField(default=0)),
                ("institution_override", models.CharField(blank=True, max_length=200, null=True)),
                ("degree_override", models.CharField(blank=True, max_length=200, null=True)),
                ("start_date_override", models.DateField(blank=True, null=True)),
                ("end_date_override", models.DateField(blank=True, null=True)),
                (
                    "education",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resume_educations",
                        to="profiles.education",
                    ),
                ),
                (
                    "resume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="educations",
                        to="resumes.resume",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume", "education"), name="resume_education_unique_source"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ResumeLanguage",
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
                ("included", models.BooleanField(default=True)),
                ("position", models.PositiveIntegerField(default=0)),
                ("name_override", models.CharField(blank=True, max_length=100, null=True)),
                ("proficiency_override", models.CharField(blank=True, max_length=20, null=True)),
                (
                    "language",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resume_languages",
                        to="profiles.language",
                    ),
                ),
                (
                    "resume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="languages",
                        to="resumes.resume",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume", "language"), name="resume_language_unique_source"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ResumeProject",
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
                ("included", models.BooleanField(default=True)),
                ("position", models.PositiveIntegerField(default=0)),
                ("name_override", models.CharField(blank=True, max_length=200, null=True)),
                ("description_override", models.TextField(blank=True, null=True)),
                ("url_override", models.URLField(blank=True, null=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resume_projects",
                        to="profiles.project",
                    ),
                ),
                (
                    "resume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="projects",
                        to="resumes.resume",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume", "project"), name="resume_project_unique_source"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ResumeSkill",
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
                ("included", models.BooleanField(default=True)),
                ("position", models.PositiveIntegerField(default=0)),
                ("label_override", models.CharField(blank=True, max_length=200, null=True)),
                (
                    "concept",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="resume_skills",
                        to="skills.skillconcept",
                    ),
                ),
                (
                    "resume",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="skills",
                        to="resumes.resume",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume", "concept"), name="resume_skill_unique_concept"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ResumeExperienceHighlight",
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
                ("included", models.BooleanField(default=True)),
                ("position", models.PositiveIntegerField(blank=True, null=True)),
                ("text_override", models.TextField(blank=True, null=True)),
                (
                    "highlight",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resume_highlights",
                        to="profiles.highlight",
                    ),
                ),
                (
                    "resume_experience",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="highlights",
                        to="resumes.resumeexperience",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("resume_experience", "highlight"),
                        name="resume_experience_highlight_unique_source",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(included=False)
                        | models.Q(position__isnull=False)
                        | models.Q(text_override__isnull=False, text_override__regex=r"\S"),
                        name="resume_experience_highlight_sparse_state",
                    ),
                ],
            },
        ),
    ]
