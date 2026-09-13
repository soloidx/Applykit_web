"""Deployment checks for shared skill catalog integrity.

The database cannot express every catalog invariant, so the catalog is
verified whenever database checks run, such as during a deployment gate. Any
drift is reported as a hard error: a release must not ship a catalog that the
transactional domain operations cannot keep consistent.
"""

from typing import Any

from django.core.checks import Error, Tags, register
from django.db import connection

from apps.skills.services import skill_catalog_issues

__all__ = ["skill_catalog_check"]


@register(Tags.database)
def skill_catalog_check(
    app_configs: Any,
    *,
    databases: Any = None,
    **kwargs: Any,
) -> list[Error]:
    if not databases:
        return []
    if not _catalog_tables_present():
        # An unmigrated or partially migrated database has no catalog to
        # verify; that is not drift.
        return []
    return [Error(issue, id="skills.E001") for issue in skill_catalog_issues()]


def _catalog_tables_present() -> bool:
    from apps.applications.models import ApplicationSkillRequirement
    from apps.profiles.models import ExperienceSkill, ProfileSkill, ProjectSkill
    from apps.skills.models import SkillAlias, SkillConcept

    tables = set(connection.introspection.table_names())
    required = {
        SkillConcept._meta.db_table,
        SkillAlias._meta.db_table,
        ProfileSkill._meta.db_table,
        ExperienceSkill._meta.db_table,
        ProjectSkill._meta.db_table,
        ApplicationSkillRequirement._meta.db_table,
    }
    return required <= tables
