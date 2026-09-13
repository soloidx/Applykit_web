from django.apps import AppConfig


class SkillsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.skills"
    label = "skills"

    def ready(self) -> None:
        from apps.skills import checks  # noqa: F401
