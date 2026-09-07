from django.contrib import admin

from apps.ai.models import AIOperationAudit, ConsentPreference


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
