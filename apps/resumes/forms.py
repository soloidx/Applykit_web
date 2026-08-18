from typing import Any, cast

from django import forms


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
