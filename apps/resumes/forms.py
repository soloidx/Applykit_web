from dataclasses import dataclass
from typing import Any, cast

from django import forms

from apps.profiles.models import (
    CandidateProfile,
    ExperienceSkill,
    Language,
    ProfileSkill,
    ProjectSkill,
)
from apps.resumes.models import Resume, ResumeSection
from apps.skills.models import SkillConcept


class ResumeHeaderForm(forms.Form):
    contact_email = forms.EmailField(required=False, label="Contact email")
    full_name = forms.CharField(required=False, max_length=200, label="Full name")
    professional_title = forms.CharField(required=False, max_length=200, label="Professional title")
    professional_summary = forms.CharField(
        required=False,
        widget=forms.Textarea,
        label="Professional summary",
    )
    phone_number = forms.CharField(required=False, max_length=32, label="Phone number")
    location = forms.CharField(required=False, max_length=200, label="Location")
    linkedin_url = forms.URLField(required=False, label="LinkedIn URL")
    portfolio_url = forms.URLField(required=False, label="Portfolio URL")
    contact_email_inherit = forms.BooleanField(required=False, label="Inherit contact email")
    full_name_inherit = forms.BooleanField(required=False, label="Inherit full name")
    professional_title_inherit = forms.BooleanField(
        required=False, label="Inherit professional title"
    )
    professional_summary_inherit = forms.BooleanField(
        required=False, label="Inherit professional summary"
    )
    phone_number_inherit = forms.BooleanField(required=False, label="Inherit phone number")
    location_inherit = forms.BooleanField(required=False, label="Inherit location")
    linkedin_url_inherit = forms.BooleanField(required=False, label="Inherit LinkedIn URL")
    portfolio_url_inherit = forms.BooleanField(required=False, label="Inherit portfolio URL")

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        for field_name in self.base_fields:
            if field_name.endswith("_inherit"):
                continue
            if cleaned_data.get(f"{field_name}_inherit") or not cleaned_data.get(field_name):
                cleaned_data[field_name] = None
        return cleaned_data


class ResumeDraftForm(ResumeHeaderForm):
    def __init__(self, *args: Any, resume: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.resume = resume
        for section in resume.sections.all():
            self.fields[f"section_{section.kind}_position"] = forms.IntegerField(
                required=True,
                min_value=0,
                initial=section.position,
                label=f"{section.get_kind_display()} section position",
            )
        for item in resume.experiences.select_related("experience").all():
            prefix = f"experience_{item.pk}"
            self._add_item_fields(
                prefix,
                item,
                {
                    "role_override": ("Role", 200),
                    "organization_override": ("Organization", 200),
                    "location_override": ("Location", 200),
                    "description_override": ("Description", None),
                },
            )
            self.fields[f"{prefix}_start_date_override"] = forms.DateField(
                required=False,
                initial=item.start_date_override,
                label="Start date",
                widget=forms.DateInput(attrs={"type": "date"}),
            )
            self.fields[f"{prefix}_end_date_override"] = forms.DateField(
                required=False,
                initial=item.end_date_override,
                label="End date",
                widget=forms.DateInput(attrs={"type": "date"}),
            )
            highlight_overlays = {
                highlight.highlight_id: highlight for highlight in item.highlights.all()
            }
            for highlight in item.experience.highlights.all():
                highlight_prefix = f"highlight_{item.pk}_{highlight.pk}"
                overlay = highlight_overlays.get(highlight.pk)
                self.fields[f"{highlight_prefix}_included"] = forms.BooleanField(
                    required=False,
                    initial=overlay.included if overlay is not None else True,
                    label="Include highlight",
                )
                self.fields[f"{highlight_prefix}_position"] = forms.IntegerField(
                    required=True,
                    min_value=0,
                    initial=(
                        overlay.position
                        if overlay is not None and overlay.position is not None
                        else highlight.position
                    ),
                    label="Highlight position",
                )
                self.fields[f"{highlight_prefix}_text_override"] = forms.CharField(
                    required=False,
                    initial=overlay.text_override if overlay is not None else "",
                    label="Highlight text override",
                    widget=forms.Textarea,
                )
        for item in resume.projects.select_related("project").all():
            prefix = f"project_{item.pk}"
            self._add_item_fields(
                prefix,
                item,
                {
                    "name_override": ("Name", 200),
                    "description_override": ("Description", None),
                    "url_override": ("URL", None),
                },
            )
        for item in resume.educations.select_related("education").all():
            prefix = f"education_{item.pk}"
            self._add_item_fields(
                prefix,
                item,
                {
                    "institution_override": ("Institution", 200),
                    "degree_override": ("Degree", 200),
                },
            )
            self.fields[f"{prefix}_start_date_override"] = forms.DateField(
                required=False,
                initial=item.start_date_override,
                label="Start date",
                widget=forms.DateInput(attrs={"type": "date"}),
            )
            self.fields[f"{prefix}_end_date_override"] = forms.DateField(
                required=False,
                initial=item.end_date_override,
                label="End date",
                widget=forms.DateInput(attrs={"type": "date"}),
            )
        for item in resume.languages.select_related("language").all():
            prefix = f"language_{item.pk}"
            self._add_item_fields(
                prefix,
                item,
                {
                    "name_override": ("Name", 100),
                    "proficiency_override": ("Proficiency", 20),
                },
            )
        for item in resume.skills.select_related("concept").all():
            prefix = f"skill_{item.pk}"
            self._add_item_fields(prefix, item, {"label_override": ("Label", 200)})

    def _add_item_fields(
        self,
        prefix: str,
        item: Any,
        overrides: dict[str, tuple[str, int | None]],
    ) -> None:
        self.fields[f"{prefix}_included"] = forms.BooleanField(
            required=False,
            initial=item.included,
            label="Include",
        )
        self.fields[f"{prefix}_position"] = forms.IntegerField(
            required=True,
            min_value=0,
            initial=item.position,
            label="Position",
        )
        for field_name, (label, max_length) in overrides.items():
            field_kwargs: dict[str, object] = {
                "required": False,
                "initial": getattr(item, field_name),
                "label": label,
            }
            if max_length is not None:
                field_kwargs["max_length"] = max_length
            if field_name == "description_override":
                field_kwargs["widget"] = forms.Textarea
            field_class = forms.URLField if field_name == "url_override" else forms.CharField
            self.fields[f"{prefix}_{field_name}"] = field_class(**field_kwargs)  # type: ignore[arg-type]

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        raw_section_positions = [
            cleaned_data.get(f"section_{kind}_position")
            for kind in self.resume.sections.values_list("kind", flat=True)
            if f"section_{kind}_position" in self.data
        ]
        section_positions: list[int] = [
            cast(int, position) for position in raw_section_positions if isinstance(position, int)
        ]
        if section_positions and sorted(section_positions) != list(range(len(section_positions))):
            raise forms.ValidationError("Section positions must be contiguous and unique.")
        for prefix in ("experience", "project", "education", "language", "skill"):
            raw_positions = [
                cleaned_data.get(f"{prefix}_{item.pk}_position")
                for item in getattr(self.resume, f"{prefix}s").all()
                if f"{prefix}_{item.pk}_position" in self.data
            ]
            highlight_positions: list[int] = [
                cast(int, position) for position in raw_positions if isinstance(position, int)
            ]
            if highlight_positions and sorted(highlight_positions) != list(
                range(len(highlight_positions))
            ):
                raise forms.ValidationError(
                    f"{prefix.title()} positions must be contiguous and unique."
                )
        for item in self.resume.experiences.all():
            raw_positions = [
                cleaned_data.get(f"highlight_{item.pk}_{highlight.pk}_position")
                for highlight in item.experience.highlights.all()
                if f"highlight_{item.pk}_{highlight.pk}_position" in self.data
            ]
            positions: list[int] = [
                cast(int, position) for position in raw_positions if isinstance(position, int)
            ]
            if positions and sorted(positions) != list(range(len(positions))):
                raise forms.ValidationError("Highlight positions must be contiguous and unique.")
        return cleaned_data


class ResumeSectionForm(forms.Form):
    kind = forms.ChoiceField(choices=ResumeSection.Kind.choices)
    position = forms.IntegerField(min_value=0)


class _ResumeItemForm(forms.Form):
    source_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    included = forms.BooleanField(required=False)
    position = forms.IntegerField(min_value=0)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        for field_name in self.base_fields:
            if not field_name.endswith("_override"):
                continue
            if cleaned_data.get(f"{field_name}_inherit") or not cleaned_data.get(field_name):
                cleaned_data[field_name] = None
        return cleaned_data


class ResumeExperienceForm(_ResumeItemForm):
    role_override = forms.CharField(required=False, max_length=200)
    role_override_inherit = forms.BooleanField(required=False)
    organization_override = forms.CharField(required=False, max_length=200)
    organization_override_inherit = forms.BooleanField(required=False)
    location_override = forms.CharField(required=False, max_length=200)
    location_override_inherit = forms.BooleanField(required=False)
    start_date_override = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    start_date_override_inherit = forms.BooleanField(required=False)
    end_date_override = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date_override_inherit = forms.BooleanField(required=False)
    description_override = forms.CharField(required=False, widget=forms.Textarea)
    description_override_inherit = forms.BooleanField(required=False)


class ResumeExperienceHighlightForm(forms.Form):
    experience_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    source_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    included = forms.BooleanField(required=False)
    position = forms.IntegerField(required=False, min_value=0)
    text_override = forms.CharField(required=False, widget=forms.Textarea)
    text_override_inherit = forms.BooleanField(required=False)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if cleaned_data.get("text_override_inherit") or not cleaned_data.get("text_override"):
            cleaned_data["text_override"] = None
        if not cleaned_data.get("included"):
            cleaned_data["position"] = None
        return cleaned_data


class ResumeProjectForm(_ResumeItemForm):
    name_override = forms.CharField(required=False, max_length=200)
    name_override_inherit = forms.BooleanField(required=False)
    description_override = forms.CharField(required=False, widget=forms.Textarea)
    description_override_inherit = forms.BooleanField(required=False)
    url_override = forms.URLField(required=False)
    url_override_inherit = forms.BooleanField(required=False)


class ResumeEducationForm(_ResumeItemForm):
    institution_override = forms.CharField(required=False, max_length=200)
    institution_override_inherit = forms.BooleanField(required=False)
    degree_override = forms.CharField(required=False, max_length=200)
    degree_override_inherit = forms.BooleanField(required=False)
    start_date_override = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    start_date_override_inherit = forms.BooleanField(required=False)
    end_date_override = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date_override_inherit = forms.BooleanField(required=False)


class ResumeLanguageForm(_ResumeItemForm):
    name_override = forms.CharField(required=False, max_length=100)
    name_override_inherit = forms.BooleanField(required=False)
    proficiency_override = forms.ChoiceField(
        required=False,
        choices=[("", "---------"), *Language.Proficiency.choices],
    )
    proficiency_override_inherit = forms.BooleanField(required=False)


class ResumeSkillForm(_ResumeItemForm):
    label_override = forms.CharField(required=False, max_length=200)
    label_override_inherit = forms.BooleanField(required=False)


class _IdentityFormSet(forms.BaseFormSet):
    identity_field = "source_id"
    included_field = "included"
    position_field = "position"

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        identities: set[int] = set()
        positions: list[int] = []
        for form in self.forms:
            if not form.cleaned_data:
                continue
            identity = form.cleaned_data[self.identity_field]
            if identity in identities:
                raise forms.ValidationError("Each source may appear only once.")
            identities.add(identity)
            if form.cleaned_data.get(self.included_field):
                position = form.cleaned_data.get(self.position_field)
                if position is None:
                    raise forms.ValidationError("Included records need a position.")
                positions.append(position)
        if sorted(positions) != list(range(len(positions))):
            raise forms.ValidationError("Included record positions must be contiguous and unique.")


class _HighlightFormSet(forms.BaseFormSet):
    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        identities: set[tuple[int, int]] = set()
        positions_by_experience: dict[int, list[int]] = {}
        for form in self.forms:
            if not form.cleaned_data:
                continue
            identity = (form.cleaned_data["experience_id"], form.cleaned_data["source_id"])
            if identity in identities:
                raise forms.ValidationError("Each highlight may appear only once per experience.")
            identities.add(identity)
            if form.cleaned_data.get("included"):
                position = form.cleaned_data.get("position")
                if position is None:
                    raise forms.ValidationError("Included highlights need a position.")
                positions_by_experience.setdefault(identity[0], []).append(position)
        for positions in positions_by_experience.values():
            if sorted(positions) != list(range(len(positions))):
                raise forms.ValidationError("Highlight positions must be contiguous and unique.")


ResumeSectionFormSet = forms.formset_factory(ResumeSectionForm, extra=0)
ResumeExperienceFormSet = forms.formset_factory(
    ResumeExperienceForm, formset=_IdentityFormSet, extra=0
)
ResumeExperienceHighlightFormSet = forms.formset_factory(
    ResumeExperienceHighlightForm,
    formset=_HighlightFormSet,
    extra=0,
)
ResumeProjectFormSet = forms.formset_factory(ResumeProjectForm, formset=_IdentityFormSet, extra=0)
ResumeEducationFormSet = forms.formset_factory(
    ResumeEducationForm, formset=_IdentityFormSet, extra=0
)
ResumeLanguageFormSet = forms.formset_factory(ResumeLanguageForm, formset=_IdentityFormSet, extra=0)
ResumeSkillFormSet = forms.formset_factory(ResumeSkillForm, formset=_IdentityFormSet, extra=0)


@dataclass
class ResumeDraftForms:
    header: ResumeHeaderForm
    sections: Any
    experiences: Any
    highlights: Any
    projects: Any
    educations: Any
    languages: Any
    skills: Any

    def is_valid(self) -> bool:
        valid = True
        for formset in (
            self.header,
            self.sections,
            self.experiences,
            self.highlights,
            self.projects,
            self.educations,
            self.languages,
            self.skills,
        ):
            valid = formset.is_valid() and valid
        return valid

    def as_draft(self) -> dict[str, Any]:
        return {
            "header": self.header.cleaned_data,
            "sections": [form.cleaned_data for form in self.sections.forms],
            "experiences": [form.cleaned_data for form in self.experiences.forms],
            "highlights": [form.cleaned_data for form in self.highlights.forms],
            "projects": [form.cleaned_data for form in self.projects.forms],
            "educations": [form.cleaned_data for form in self.educations.forms],
            "languages": [form.cleaned_data for form in self.languages.forms],
            "skills": [form.cleaned_data for form in self.skills.forms],
        }


def _override_initial(override: Any, source: Any) -> dict[str, Any]:
    return {"value": source if override is None else override, "inherit": override is None}


def _item_initial(item: Any, source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    initial: dict[str, Any] = {
        "source_id": source.pk,
        "included": item is not None and item.included,
        "position": item.position if item is not None else source.position,
    }
    for field_name in fields:
        override = getattr(item, field_name) if item is not None else None
        values = _override_initial(override, getattr(source, field_name.removesuffix("_override")))
        initial[field_name] = values["value"]
        initial[f"{field_name}_inherit"] = values["inherit"]
    return initial


def build_resume_forms(
    *, resume: Resume, data: Any = None, default_draft: dict[str, Any] | None = None
) -> ResumeDraftForms:
    profile = CandidateProfile.objects.get(account=resume.application.account)
    experience_overlays = {item.experience_id: item for item in resume.experiences.all()}
    project_overlays = {item.project_id: item for item in resume.projects.all()}
    education_overlays = {item.education_id: item for item in resume.educations.all()}
    language_overlays = {item.language_id: item for item in resume.languages.all()}
    skill_overlays = {item.concept_id: item for item in resume.skills.all()}
    initial_experiences = [
        _item_initial(
            experience_overlays.get(source.pk),
            source,
            (
                "role_override",
                "organization_override",
                "location_override",
                "start_date_override",
                "end_date_override",
                "description_override",
            ),
        )
        for source in profile.experiences.all()
    ]
    initial_projects = [
        _item_initial(
            project_overlays.get(source.pk),
            source,
            ("name_override", "description_override", "url_override"),
        )
        for source in profile.projects.all()
    ]
    initial_educations = [
        _item_initial(
            education_overlays.get(source.pk),
            source,
            (
                "institution_override",
                "degree_override",
                "start_date_override",
                "end_date_override",
            ),
        )
        for source in profile.educations.all()
    ]
    initial_languages = [
        _item_initial(
            language_overlays.get(source.pk), source, ("name_override", "proficiency_override")
        )
        for source in profile.languages.all()
    ]
    concept_ids = (
        set(profile.profile_skills.values_list("concept_id", flat=True))
        | set(
            ExperienceSkill.objects.filter(experience__profile=profile).values_list(
                "concept_id", flat=True
            )
        )
        | set(
            ProjectSkill.objects.filter(project__profile=profile).values_list(
                "concept_id", flat=True
            )
        )
    )
    from apps.resumes.services import _skill_initialization_order

    existing_skill_order = [item.concept_id for item in resume.skills.order_by("position", "id")]
    skill_order = existing_skill_order + [
        concept_id
        for concept_id in _skill_initialization_order(resume.application, profile)
        if concept_id not in existing_skill_order
    ]
    concepts = {concept.pk: concept for concept in SkillConcept.objects.filter(pk__in=concept_ids)}
    labels: dict[int, str] = {}
    for skills in (
        ProfileSkill.objects.filter(profile=profile).order_by("position", "id"),
        ExperienceSkill.objects.filter(experience__profile=profile).order_by(
            "experience__position", "experience__id", "position", "id"
        ),
        ProjectSkill.objects.filter(project__profile=profile).order_by(
            "project__position", "project__id", "position", "id"
        ),
    ):
        for skill in skills:
            labels.setdefault(skill.concept_id, skill.label)
    initial_skills = [
        {
            "source_id": concept.pk,
            "included": concept_id in skill_overlays and skill_overlays[concept_id].included,
            "position": skill_overlays[concept_id].position
            if concept_id in skill_overlays
            else index,
            "label_override": labels.get(concept_id, concept.canonical_name),
            "label_override_inherit": (
                concept_id not in skill_overlays
                or skill_overlays[concept_id].label_override is None
            ),
        }
        for index, concept_id in enumerate(skill_order)
        if (concept := concepts.get(concept_id)) is not None
    ]
    initial_highlights = []
    highlight_overlays = {
        (item.experience_id, child.highlight_id): child
        for item in resume.experiences.all()
        for child in item.highlights.all()
    }
    for experience in profile.experiences.all():
        for highlight in experience.highlights.all():
            child = highlight_overlays.get((experience.pk, highlight.pk))
            initial_highlights.append(
                {
                    "experience_id": experience.pk,
                    "source_id": highlight.pk,
                    "included": child.included if child is not None else True,
                    "position": (
                        child.position
                        if child is not None and child.position is not None
                        else highlight.position
                    ),
                    "text_override": (
                        highlight.text
                        if child is None or child.text_override is None
                        else child.text_override
                    ),
                    "text_override_inherit": child is None or child.text_override is None,
                }
            )

    header_initial: dict[str, Any] = {}
    for field in (
        "contact_email",
        "full_name",
        "professional_title",
        "professional_summary",
        "phone_number",
        "location",
        "linkedin_url",
        "portfolio_url",
    ):
        override = getattr(resume, f"{field}_override")
        header_initial[field] = _effective_initial(override, getattr(profile, field))
        header_initial[f"{field}_inherit"] = override is None

    if default_draft is not None:
        header_initial.update(default_draft["header"])
        for field in default_draft["header"]:
            header_initial[f"{field}_inherit"] = True
        section_initial = default_draft["sections"]
        defaults_by_set = {
            "experiences": default_draft["experiences"],
            "projects": default_draft["projects"],
            "educations": default_draft["educations"],
            "languages": default_draft["languages"],
            "skills": default_draft["skills"],
        }
        initial_by_set = {
            "experiences": initial_experiences,
            "projects": initial_projects,
            "educations": initial_educations,
            "languages": initial_languages,
            "skills": initial_skills,
        }
        for name, rows in initial_by_set.items():
            rows_by_id = {row["source_id"]: row for row in defaults_by_set[name]}
            for row in rows:
                default_row = rows_by_id.get(row["source_id"])
                if default_row is None:
                    continue
                row.update(default_row)
                for field in default_row:
                    if field.endswith("_override"):
                        row[f"{field}_inherit"] = True
        default_highlights = {
            (row["experience_id"], row["source_id"]): row for row in default_draft["highlights"]
        }
        for row in initial_highlights:
            default_row = default_highlights.get((row["experience_id"], row["source_id"]))
            if default_row is not None:
                row.update(default_row)
                row["text_override_inherit"] = True
    else:
        section_initial = list(resume.sections.values("kind", "position"))

    return ResumeDraftForms(
        header=ResumeHeaderForm(data, prefix="header")
        if data is not None
        else ResumeHeaderForm(prefix="header", initial=header_initial),
        sections=ResumeSectionFormSet(data, prefix="sections", initial=section_initial),
        experiences=ResumeExperienceFormSet(
            data, prefix="experiences", initial=initial_experiences
        ),
        highlights=ResumeExperienceHighlightFormSet(
            data, prefix="highlights", initial=initial_highlights
        ),
        projects=ResumeProjectFormSet(data, prefix="projects", initial=initial_projects),
        educations=ResumeEducationFormSet(data, prefix="educations", initial=initial_educations),
        languages=ResumeLanguageFormSet(data, prefix="languages", initial=initial_languages),
        skills=ResumeSkillFormSet(data, prefix="skills", initial=initial_skills),
    )


def _effective_initial(override: Any, source: Any) -> Any:
    return source if override is None else override
