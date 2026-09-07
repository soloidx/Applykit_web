from django.contrib import admin

from apps.ai.models import ConsentPreference


@admin.register(ConsentPreference)
class ConsentPreferenceAdmin(admin.ModelAdmin):
    list_display = ("account", "accepted", "policy")
    search_fields = ("account__email",)
    autocomplete_fields = ("account",)
