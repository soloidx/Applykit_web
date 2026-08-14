import unicodedata
from typing import Any

from django.apps.registry import Apps as StateApps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def normalize_skill_label(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def resolve_concept(SkillAlias: Any, SkillConcept: Any, *, key: str, label: str) -> Any:
    alias = SkillAlias.objects.filter(normalized_value=key).select_related("concept").first()
    if alias:
        return alias.concept

    concept = SkillConcept.objects.filter(canonical_key=key).first()
    if concept is None:
        concept = SkillConcept.objects.create(canonical_name=label, canonical_key=key)
    SkillAlias.objects.get_or_create(
        normalized_value=key,
        defaults={
            "concept": concept,
            "display_name": label,
            "is_canonical": True,
        },
    )
    return concept


def backfill_profile_skills(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    ProfileSkill = apps.get_model("profiles", "ProfileSkill")
    SkillAlias = apps.get_model("skills", "SkillAlias")
    SkillConcept = apps.get_model("skills", "SkillConcept")

    for profile_skill in ProfileSkill.objects.order_by("pk"):
        label = profile_skill.name.strip()
        # Preserve the old uniqueness boundary so valid legacy rows cannot
        # collapse into one profile association during the contract step.
        legacy_key = profile_skill.normalized_name or label.casefold()
        normalized_label = normalize_skill_label(label)
        concept = resolve_concept(
            SkillAlias,
            SkillConcept,
            key=normalized_label,
            label=label,
        )
        existing = ProfileSkill.objects.filter(
            profile_id=profile_skill.profile_id,
            concept_id=concept.pk,
        ).exclude(pk=profile_skill.pk).first()
        if existing:
            existing_legacy_key = existing.normalized_name or existing.name.strip().casefold()
            if existing_legacy_key == normalized_label:
                # Keep both valid legacy rows while retaining global catalog reuse.
                concept = resolve_concept(
                    SkillAlias,
                    SkillConcept,
                    key=legacy_key,
                    label=label,
                )
            else:
                # The earlier row used the catalog concept first; move that
                # row to its legacy key and keep this row on the shared one.
                existing_concept = resolve_concept(
                    SkillAlias,
                    SkillConcept,
                    key=existing_legacy_key,
                    label=existing.name.strip(),
                )
                ProfileSkill.objects.filter(pk=existing.pk).update(concept=existing_concept)

        ProfileSkill.objects.filter(pk=profile_skill.pk).update(
            label=label,
            normalized_label=normalized_label,
            concept=concept,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("profiles", "0007_profile_skill_expand"),
    ]

    operations = [
        migrations.RunPython(backfill_profile_skills, migrations.RunPython.noop),
    ]
