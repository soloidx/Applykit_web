from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account
from apps.profiles.forms import SkillAssociationForm
from apps.skills.models import SkillAlias, SkillConcept, clean_skill_label
from apps.skills.services import (
    create_skill_alias,
    normalize_skill_label,
    rename_skill_alias,
    rename_skill_concept,
    resolve_skill_label,
    skill_catalog_issues,
)

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
def test_direct_concept_rename_outside_the_skills_domain_is_rejected() -> None:
    concept, _ = resolve_skill_label("Node.js")

    concept.canonical_name = "NodeJS"
    with pytest.raises(ValidationError):
        concept.save()

    concept.refresh_from_db()
    assert concept.canonical_name == "Node.js"
    assert concept.canonical_key == "node.js"
    assert (
        SkillAlias.objects.filter(concept=concept, is_canonical=True).get().display_name
        == "Node.js"
    )


@pytest.mark.django_db
def test_direct_canonical_alias_editing_is_rejected() -> None:
    concept, _ = resolve_skill_label("Node.js")
    canonical_alias = concept.aliases.get(is_canonical=True)

    canonical_alias.display_name = "nodejs"
    with pytest.raises(ValidationError):
        canonical_alias.save()

    canonical_alias.refresh_from_db()
    assert canonical_alias.display_name == "Node.js"
    assert concept.aliases.filter(is_canonical=True).get().normalized_value == "node.js"


@pytest.mark.django_db
def test_direct_alias_promotion_and_demotion_are_rejected() -> None:
    concept, _ = resolve_skill_label("Node.js")
    alias = SkillAlias.objects.create(concept=concept, display_name="nodejs")
    canonical_alias = concept.aliases.get(is_canonical=True)

    alias.is_canonical = True
    with pytest.raises(ValidationError):
        alias.save()
    canonical_alias.is_canonical = False
    with pytest.raises(ValidationError):
        canonical_alias.save()

    alias.refresh_from_db()
    canonical_alias.refresh_from_db()
    assert alias.is_canonical is False
    assert canonical_alias.is_canonical is True


@pytest.mark.django_db
def test_direct_alias_reassignment_to_another_concept_is_rejected() -> None:
    first, _ = resolve_skill_label("Python")
    second, _ = resolve_skill_label("Django")
    alias = SkillAlias.objects.create(concept=first, display_name="py")

    alias.concept = second
    with pytest.raises(ValidationError):
        alias.save()

    alias.refresh_from_db()
    assert alias.concept == first


@pytest.mark.django_db
def test_create_skill_alias_adds_shared_wording_for_one_concept() -> None:
    concept, _ = resolve_skill_label("Node.js")

    alias, created = create_skill_alias(concept=concept, display_name="  NodeJS  ")

    assert created is True
    assert alias.display_name == "NodeJS"
    assert alias.normalized_value == "nodejs"
    assert alias.concept == concept
    assert alias.is_canonical is False
    assert resolve_skill_label("nodejs")[0] == concept


@pytest.mark.django_db
def test_create_skill_alias_reuses_the_existing_alias_for_the_same_concept() -> None:
    concept, _ = resolve_skill_label("Node.js")
    first, _ = create_skill_alias(concept=concept, display_name="nodejs")

    second, created = create_skill_alias(concept=concept, display_name="NodeJS")

    assert created is False
    assert second.pk == first.pk
    assert SkillAlias.objects.filter(normalized_value="nodejs").count() == 1


@pytest.mark.django_db
def test_create_skill_alias_returns_the_canonical_alias_for_canonical_wording() -> None:
    concept, _ = resolve_skill_label("Node.js")

    alias, created = create_skill_alias(concept=concept, display_name="node.js")

    assert created is False
    assert alias.is_canonical is True
    assert alias.normalized_value == "node.js"
    assert SkillAlias.objects.filter(concept=concept).count() == 1


@pytest.mark.django_db
def test_create_skill_alias_rejects_another_concepts_alias() -> None:
    first, _ = resolve_skill_label("Python")
    second, _ = resolve_skill_label("Django")
    SkillAlias.objects.create(concept=first, display_name="py")

    with pytest.raises(ValidationError):
        create_skill_alias(concept=second, display_name="py")


@pytest.mark.django_db
def test_create_skill_alias_rejects_another_concepts_canonical_name() -> None:
    first, _ = resolve_skill_label("Python")
    second, _ = resolve_skill_label("Django")

    with pytest.raises(ValidationError):
        create_skill_alias(concept=first, display_name="Django")

    assert SkillAlias.objects.filter(normalized_value="django").count() == 1


@pytest.mark.django_db
def test_create_skill_alias_rejects_blank_wording() -> None:
    concept, _ = resolve_skill_label("Python")

    with pytest.raises(ValidationError):
        create_skill_alias(concept=concept, display_name="   ")

    assert SkillAlias.objects.count() == 1


@pytest.mark.django_db
def test_rename_skill_alias_corrects_noncanonical_wording() -> None:
    concept, _ = resolve_skill_label("Node.js")
    alias = SkillAlias.objects.create(concept=concept, display_name="nodejs")

    renamed = rename_skill_alias(alias=alias, display_name="  Node JS  ")

    assert renamed.pk == alias.pk
    assert renamed.display_name == "Node JS"
    assert renamed.normalized_value == "node js"
    assert renamed.concept_id == concept.pk
    assert renamed.is_canonical is False


@pytest.mark.django_db
def test_rename_skill_alias_rejects_the_canonical_alias() -> None:
    concept, _ = resolve_skill_label("Node.js")
    canonical_alias = concept.aliases.get(is_canonical=True)

    with pytest.raises(ValidationError):
        rename_skill_alias(alias=canonical_alias, display_name="NodeJS")

    canonical_alias.refresh_from_db()
    assert canonical_alias.display_name == "Node.js"


@pytest.mark.django_db
def test_rename_skill_alias_rejects_another_concepts_wording() -> None:
    concept, _ = resolve_skill_label("Node.js")
    other, _ = resolve_skill_label("Django")
    alias = SkillAlias.objects.create(concept=concept, display_name="nodejs")

    with pytest.raises(ValidationError):
        rename_skill_alias(alias=alias, display_name="Django")

    alias.refresh_from_db()
    assert alias.display_name == "nodejs"


@pytest.mark.django_db
def test_rename_skill_alias_rejects_another_concepts_alias() -> None:
    concept, _ = resolve_skill_label("Node.js")
    other, _ = resolve_skill_label("Python")
    SkillAlias.objects.create(concept=other, display_name="py")
    alias = SkillAlias.objects.create(concept=concept, display_name="nodejs")

    with pytest.raises(ValidationError):
        rename_skill_alias(alias=alias, display_name="py")

    alias.refresh_from_db()
    assert alias.display_name == "nodejs"


@pytest.mark.django_db
def test_rename_skill_alias_rejects_a_duplicate_alias_for_the_same_concept() -> None:
    concept, _ = resolve_skill_label("Node.js")
    SkillAlias.objects.create(concept=concept, display_name="nodejs")
    duplicate = SkillAlias.objects.create(concept=concept, display_name="node")

    with pytest.raises(ValidationError):
        rename_skill_alias(alias=duplicate, display_name="nodejs")

    duplicate.refresh_from_db()
    assert duplicate.display_name == "node"


@pytest.mark.django_db
def test_rename_skill_alias_rejects_blank_wording() -> None:
    concept, _ = resolve_skill_label("Node.js")
    alias = SkillAlias.objects.create(concept=concept, display_name="nodejs")

    with pytest.raises(ValidationError):
        rename_skill_alias(alias=alias, display_name="   ")

    alias.refresh_from_db()
    assert alias.display_name == "nodejs"


@pytest.mark.django_db(transaction=True)
def test_concurrent_alias_creation_converges_on_one_shared_alias() -> None:
    concept, _ = resolve_skill_label("Elixir")
    barrier = Barrier(2)

    def create() -> tuple[int, bool]:
        close_old_connections()
        try:
            barrier.wait()
            alias, created = create_skill_alias(concept=concept, display_name="ElixirLang")
            return alias.pk, created
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))

    alias_id = SkillAlias.objects.get(normalized_value="elixirlang").pk
    assert {result[0] for result in results} == {alias_id}
    assert sum(result[1] for result in results) == 1
    assert SkillAlias.objects.filter(normalized_value="elixirlang").count() == 1
    assert all(alias.concept_id == concept.pk for alias in SkillAlias.objects.all())


@pytest.mark.django_db(transaction=True)
def test_concurrent_distinct_alias_creation_preserves_each_wording() -> None:
    concept, _ = resolve_skill_label("Elixir")
    barrier = Barrier(2)

    def create(wording: str) -> int:
        close_old_connections()
        try:
            barrier.wait()
            alias, created = create_skill_alias(concept=concept, display_name=wording)
            assert created is True
            return alias.pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ["ElixirLang", "Exlr"]))

    assert len(set(results)) == 2
    assert SkillAlias.objects.filter(concept=concept).count() == 3


@pytest.mark.django_db(transaction=True)
def test_concurrent_concept_and_alias_creation_converge_on_one_identity() -> None:
    holder, _ = resolve_skill_label("Holder")
    barrier = Barrier(2)

    def resolve() -> str:
        close_old_connections()
        try:
            barrier.wait()
            concept, created = resolve_skill_label("Phoenix")
            return f"concept:{concept.pk}:created:{created}"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()

    def create() -> str:
        close_old_connections()
        try:
            barrier.wait()
            alias, _ = create_skill_alias(concept=holder, display_name="Phoenix")
            return f"alias:{alias.pk}:concept:{alias.concept_id}"
        except ValidationError:
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        resolve_result, create_result = list(executor.map(lambda op: op(), [resolve, create]))

    concepts = SkillConcept.objects.filter(canonical_key="phoenix")
    aliases = SkillAlias.objects.filter(normalized_value="phoenix")
    assert aliases.count() == 1
    assert concepts.count() <= 1
    if concepts.count() == 1:
        assert aliases[0].concept_id == concepts[0].pk
        assert resolve_result == f"concept:{concepts[0].pk}:created:True"
        assert create_result == "rejected"
    else:
        assert aliases[0].concept_id == holder.pk
        assert resolve_result == f"concept:{holder.pk}:created:False"
        assert create_result.startswith(f"alias:{aliases[0].pk}:concept:{holder.pk}")


@pytest.mark.django_db
def test_a_healthy_skill_catalog_reports_no_issues() -> None:
    concept, _ = resolve_skill_label("Python")
    create_skill_alias(concept=concept, display_name="py")

    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_a_missing_canonical_alias_is_detected() -> None:
    concept, _ = resolve_skill_label("Python")
    SkillAlias.objects.filter(concept=concept, is_canonical=True).delete()

    issues = skill_catalog_issues()

    assert len(issues) == 1
    assert "no canonical alias" in issues[0]
    assert "Python" in issues[0]


@pytest.mark.django_db(transaction=True)
def test_multiple_canonical_aliases_are_detected() -> None:
    concept, _ = resolve_skill_label("Python")
    SkillAlias.objects.create(concept=concept, display_name="py")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DROP INDEX skill_alias_one_canonical_per_concept")
        SkillAlias.objects.filter(concept=concept, normalized_value="py").update(is_canonical=True)
        issues = skill_catalog_issues()

    # Restore the protected state so the unique index can exist again.
    with transaction.atomic():
        SkillAlias.objects.filter(concept=concept, normalized_value="py").update(is_canonical=False)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE UNIQUE INDEX skill_alias_one_canonical_per_concept "
                "ON skills_skillalias (concept_id) WHERE is_canonical"
            )

    assert len(issues) == 1
    assert "multiple canonical aliases" in issues[0]


@pytest.mark.django_db
def test_canonical_alias_key_drift_is_detected() -> None:
    concept, _ = resolve_skill_label("Python")
    SkillAlias.objects.filter(concept=concept, is_canonical=True).update(
        display_name="Python 3", normalized_value="python 3"
    )

    issues = skill_catalog_issues()

    assert len(issues) == 1
    assert "does not match the key" in issues[0]


@pytest.mark.django_db
def test_canonical_alias_name_drift_is_detected() -> None:
    concept, _ = resolve_skill_label("Python")
    SkillAlias.objects.filter(concept=concept, is_canonical=True).update(display_name="Py")

    issues = skill_catalog_issues()

    assert len(issues) == 1
    assert "does not match the name" in issues[0]


@pytest.mark.django_db
def test_alias_wording_captured_by_another_concept_is_detected() -> None:
    python_concept, _ = resolve_skill_label("Python")
    django_concept, _ = resolve_skill_label("Django")
    create_skill_alias(concept=python_concept, display_name="py")
    # Simulate corruption outside the domain operations: the concept loses its
    # canonical alias and a foreign alias takes over its canonical wording.
    SkillAlias.objects.filter(concept=django_concept, is_canonical=True).delete()
    SkillAlias.objects.filter(concept=python_concept, normalized_value="py").update(
        display_name="Django", normalized_value="django"
    )

    issues = skill_catalog_issues()

    assert len(issues) == 2
    assert any("canonical key of another skill concept" in issue for issue in issues)


@pytest.mark.django_db
def test_direct_concept_display_casing_change_is_rejected() -> None:
    concept, _ = resolve_skill_label("Node.js")

    concept.canonical_name = "NODE.JS"
    with pytest.raises(ValidationError):
        concept.save()

    concept.refresh_from_db()
    assert concept.canonical_name == "Node.js"
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_rename_skill_concept_can_correct_display_casing() -> None:
    concept, _ = resolve_skill_label("Node.js")

    renamed = rename_skill_concept(concept=concept, canonical_name="NODE.JS")

    assert renamed.canonical_name == "NODE.JS"
    assert renamed.canonical_key == "node.js"
    canonical_alias = renamed.aliases.get(is_canonical=True)
    assert canonical_alias.display_name == "NODE.JS"
    assert canonical_alias.normalized_value == "node.js"
    assert skill_catalog_issues() == ()


@pytest.mark.django_db
def test_administrator_can_add_a_skill_alias_through_the_admin() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    concept, _ = resolve_skill_label("Node.js")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillalias_add"),
        {"concept": str(concept.pk), "display_name": "NodeJS", "_save": "Save"},
    )

    assert response.status_code == 302
    alias = SkillAlias.objects.get(normalized_value="nodejs")
    assert alias.concept == concept
    assert alias.is_canonical is False
    assert resolve_skill_label("nodejs")[0] == concept


@pytest.mark.django_db
def test_admin_cannot_add_an_alias_for_another_concepts_wording() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    first, _ = resolve_skill_label("Python")
    second, _ = resolve_skill_label("Django")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillalias_add"),
        {"concept": str(first.pk), "display_name": "Django", "_save": "Save"},
    )

    assert response.status_code == 200
    assert b"already in the shared skill namespace" in response.content
    assert SkillAlias.objects.filter(concept=first).count() == 1
    assert SkillAlias.objects.get(normalized_value="django").concept == second


@pytest.mark.django_db
def test_admin_cannot_reassign_an_alias_to_another_concept() -> None:
    administrator = Account.objects.create_superuser("admin@example.com", "a-secure-password")
    first, _ = resolve_skill_label("Python")
    second, _ = resolve_skill_label("Django")
    alias = SkillAlias.objects.create(concept=first, display_name="py")
    client = Client()
    client.force_login(administrator)

    response = client.post(
        reverse("admin:skills_skillalias_change", args=[alias.pk]),
        {"concept": str(second.pk), "display_name": "py", "_save": "Save"},
    )

    assert response.status_code == 200
    assert b"reassign" in response.content
    alias.refresh_from_db()
    assert alias.concept == first


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
