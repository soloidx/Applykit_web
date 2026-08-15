from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.profiles.forms import SkillAssociationForm
from apps.skills.models import SkillAlias, SkillConcept, clean_skill_label
from apps.skills.services import normalize_skill_label, resolve_skill_label

pytestmark = pytest.mark.integration


@pytest.mark.django_db
def test_skill_labels_are_trimmed_and_unicode_casefolded() -> None:
    assert clean_skill_label("  Node.js  ") == "Node.js"
    assert clean_skill_label("Node  JS") == "Node  JS"
    assert normalize_skill_label("  Straße  ") == "strasse"
    assert normalize_skill_label("  ") == ""


@pytest.mark.django_db
def test_skill_association_paths_share_the_label_form_adapter() -> None:
    form = SkillAssociationForm({"label": "  C++  "})

    assert form.is_valid()
    assert form.cleaned_data["label"] == "C++"
    assert set(form.fields) == {"label"}


@pytest.mark.django_db
def test_canonical_names_and_aliases_resolve_to_one_shared_concept() -> None:
    concept = SkillConcept.objects.create(canonical_name="Node.js")
    SkillAlias.objects.create(concept=concept, display_name="nodejs")

    canonical_match, canonical_created = resolve_skill_label("  NODE.JS ")
    alias_match, alias_created = resolve_skill_label("NodeJS")

    assert canonical_match == concept
    assert canonical_created is False
    assert alias_match == concept
    assert alias_created is False
    assert SkillConcept.objects.count() == 1
    assert SkillAlias.objects.filter(concept=concept, normalized_value="nodejs").exists()


@pytest.mark.django_db
def test_unknown_label_creates_a_reusable_concept_and_preserves_display_form() -> None:
    concept, created = resolve_skill_label("  TypeScript  ")

    reused, reused_created = resolve_skill_label("typescript")

    assert created is True
    assert reused_created is False
    assert reused == concept
    assert concept.canonical_name == "TypeScript"
    assert concept.canonical_key == "typescript"
    alias = SkillAlias.objects.get(concept=concept, normalized_value="typescript")
    assert alias.display_name == "TypeScript"


@pytest.mark.django_db(transaction=True)
def test_concurrent_unknown_labels_reuse_one_shared_concept() -> None:
    barrier = Barrier(2)

    def resolve() -> tuple[int, bool]:
        close_old_connections()
        try:
            barrier.wait()
            concept, created = resolve_skill_label("Elixir")
            return concept.pk, created
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: resolve(), range(2)))

    concept_id = SkillConcept.objects.get(canonical_key="elixir").pk
    assert {result[0] for result in results} == {concept_id}
    assert sum(result[1] for result in results) == 1
    assert SkillConcept.objects.filter(canonical_key="elixir").count() == 1
    assert SkillAlias.objects.filter(normalized_value="elixir").count() == 1


@pytest.mark.django_db
def test_canonical_names_and_aliases_share_one_global_namespace() -> None:
    first, _ = resolve_skill_label("Python")
    SkillAlias.objects.create(concept=first, display_name="py")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SkillConcept.objects.create(canonical_name="PY")

    assert SkillConcept.objects.count() == 1
    assert SkillAlias.objects.filter(normalized_value="py", concept=first).exists()


@pytest.mark.django_db
def test_empty_skill_labels_are_rejected() -> None:
    with pytest.raises(ValidationError):
        clean_skill_label(" \t ")
    with pytest.raises(ValidationError):
        resolve_skill_label(" \t ")


@pytest.mark.django_db
def test_administrator_can_correct_a_skill_canonical_name() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    concept, _ = resolve_skill_label("Node.js")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillconcept_change", args=[concept.pk]),
        {"canonical_name": "Node", "_save": "Save"},
    )

    assert response.status_code == 302
    concept.refresh_from_db()
    assert concept.canonical_name == "Node"
    assert concept.canonical_key == "node"
    assert resolve_skill_label("node.js")[0] == concept
    assert resolve_skill_label("node")[0] == concept


@pytest.mark.django_db
def test_administrator_can_inspect_and_create_a_skill_concept() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillconcept_add"),
        {"canonical_name": "Django", "_save": "Save"},
    )

    assert response.status_code == 302
    concept = SkillConcept.objects.get(canonical_key="django")
    assert SkillAlias.objects.filter(concept=concept, normalized_value="django").exists()


@pytest.mark.django_db
def test_administrator_cannot_correct_a_skill_to_another_concepts_alias() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    first, _ = resolve_skill_label("Python")
    second, _ = resolve_skill_label("Django")
    SkillAlias.objects.create(concept=second, display_name="framework")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillconcept_change", args=[first.pk]),
        {"canonical_name": "framework", "_save": "Save"},
    )

    assert response.status_code == 200
    assert b"already belongs to another skill concept" in response.content
    first.refresh_from_db()
    assert first.canonical_name == "Python"


@pytest.mark.django_db
def test_administrator_can_correct_a_skill_alias() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    concept, _ = resolve_skill_label("Node.js")
    alias = SkillAlias.objects.create(concept=concept, display_name="nodejs")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillalias_change", args=[alias.pk]),
        {"concept": str(concept.pk), "display_name": "NodeJS", "_save": "Save"},
    )

    assert response.status_code == 302
    alias.refresh_from_db()
    assert alias.display_name == "NodeJS"
    assert alias.normalized_value == "nodejs"
    assert resolve_skill_label("nodejs")[0] == concept
