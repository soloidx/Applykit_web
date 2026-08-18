from django.db import models


class Resume(models.Model):
    application = models.OneToOneField(
        "applications.JobApplication",
        on_delete=models.CASCADE,
        related_name="resume",
    )
    contact_email_override = models.EmailField(max_length=254, null=True, blank=True)
    full_name_override = models.CharField(max_length=200, null=True, blank=True)
    professional_title_override = models.CharField(max_length=200, null=True, blank=True)
    professional_summary_override = models.TextField(null=True, blank=True)
    phone_number_override = models.CharField(max_length=32, null=True, blank=True)
    location_override = models.CharField(max_length=200, null=True, blank=True)
    linkedin_url_override = models.URLField(null=True, blank=True)
    portfolio_url_override = models.URLField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class ResumeSection(models.Model):
    class Kind(models.TextChoices):
        SUMMARY = "summary", "Summary"
        SKILLS = "skills", "Skills"
        EXPERIENCE = "experience", "Experience"
        PROJECTS = "projects", "Projects"
        EDUCATION = "education", "Education"
        LANGUAGES = "languages", "Languages"

    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="sections")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume", "kind"],
                name="resume_section_unique_kind",
            )
        ]


class ResumeExperience(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="experiences")
    experience = models.ForeignKey(
        "profiles.Experience",
        on_delete=models.CASCADE,
        related_name="resume_experiences",
    )
    included = models.BooleanField(default=True)
    is_relevant = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    role_override = models.CharField(max_length=200, null=True, blank=True)
    organization_override = models.CharField(max_length=200, null=True, blank=True)
    location_override = models.CharField(max_length=200, null=True, blank=True)
    start_date_override = models.DateField(null=True, blank=True)
    end_date_override = models.DateField(null=True, blank=True)
    description_override = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume", "experience"],
                name="resume_experience_unique_source",
            )
        ]


class ResumeExperienceHighlight(models.Model):
    resume_experience = models.ForeignKey(
        ResumeExperience,
        on_delete=models.CASCADE,
        related_name="highlights",
    )
    highlight = models.ForeignKey(
        "profiles.Highlight",
        on_delete=models.CASCADE,
        related_name="resume_highlights",
    )
    included = models.BooleanField(default=True)
    position = models.PositiveIntegerField(null=True, blank=True)
    text_override = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume_experience", "highlight"],
                name="resume_experience_highlight_unique_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(included=False)
                    | models.Q(position__isnull=False)
                    | models.Q(text_override__isnull=False, text_override__regex=r"\S")
                ),
                name="resume_experience_highlight_sparse_state",
            ),
        ]


class ResumeEducation(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="educations")
    education = models.ForeignKey(
        "profiles.Education",
        on_delete=models.CASCADE,
        related_name="resume_educations",
    )
    included = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    institution_override = models.CharField(max_length=200, null=True, blank=True)
    degree_override = models.CharField(max_length=200, null=True, blank=True)
    start_date_override = models.DateField(null=True, blank=True)
    end_date_override = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume", "education"],
                name="resume_education_unique_source",
            )
        ]


class ResumeProject(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="projects")
    project = models.ForeignKey(
        "profiles.Project",
        on_delete=models.CASCADE,
        related_name="resume_projects",
    )
    included = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    name_override = models.CharField(max_length=200, null=True, blank=True)
    description_override = models.TextField(null=True, blank=True)
    url_override = models.URLField(null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume", "project"],
                name="resume_project_unique_source",
            )
        ]


class ResumeLanguage(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="languages")
    language = models.ForeignKey(
        "profiles.Language",
        on_delete=models.CASCADE,
        related_name="resume_languages",
    )
    included = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    name_override = models.CharField(max_length=100, null=True, blank=True)
    proficiency_override = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume", "language"],
                name="resume_language_unique_source",
            )
        ]


class ResumeSkill(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="skills")
    concept = models.ForeignKey(
        "skills.SkillConcept",
        on_delete=models.PROTECT,
        related_name="resume_skills",
    )
    included = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)
    label_override = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["resume", "concept"],
                name="resume_skill_unique_concept",
            )
        ]
