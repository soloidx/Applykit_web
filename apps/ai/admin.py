from django.contrib import admin

from apps.ai.models import (
    AIOperationAudit,
    AIOperationReservation,
    AIOperationSwitch,
    ConsentPreference,
)


@admin.register(ConsentPreference)
class ConsentPreferenceAdmin(admin.ModelAdmin):
    list_display = ("account", "accepted", "policy")
    search_fields = ("account__email",)
    autocomplete_fields = ("account",)


@admin.register(AIOperationAudit)
class AIOperationAuditAdmin(admin.ModelAdmin):
    # Content-free fields only; source content never existed to display.
    list_display = ("account", "feature", "outcome", "model", "cost", "created_at")
    list_filter = ("feature", "outcome")
    search_fields = ("account__email", "feature")
    readonly_fields = (
        "account",
        "feature",
        "consent_policy",
        "outcome",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "created_at",
    )


@admin.register(AIOperationSwitch)
class AIOperationSwitchAdmin(admin.ModelAdmin):
    # Operator-controlled fail-closed switches; absent rows mean disabled.
    list_display = ("scope", "enabled", "updated_at")
    list_filter = ("enabled",)
    fields = ("scope", "enabled")


@admin.register(AIOperationReservation)
class AIOperationReservationAdmin(admin.ModelAdmin):
    # Content-free admission ledger; read-only for operators.
    list_display = (
        "account",
        "feature",
        "status",
        "reserved_cost",
        "created_at",
    )
    list_filter = ("feature", "status")
    readonly_fields = (
        "account",
        "feature",
        "status",
        "reserved_cost",
        "created_at",
    )

    def has_add_permission(self, *args: object, **kwargs: object) -> bool:
        return False

    def has_change_permission(self, *args: object, **kwargs: object) -> bool:
        return False

    def has_delete_permission(self, *args: object, **kwargs: object) -> bool:
        return False
