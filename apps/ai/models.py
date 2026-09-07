from django.conf import settings
from django.db import models


class ConsentPreference(models.Model):
    """The AI-owned consent preference for one Account.

    An absent preference means not accepted. The stored policy identifier is
    the one under which acceptance was made; when the configured global
    consent-policy identifier changes, the acceptance no longer counts and
    renewed consent is required. No consent history is kept.
    """

    class Meta:
        verbose_name = "AI consent preference"
        verbose_name_plural = "AI consent preferences"

    account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_consent",
    )
    accepted = models.BooleanField(default=False)
    policy = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return f"{self.account.email}: {'accepted' if self.accepted else 'not accepted'}"


class AIOperationAudit(models.Model):
    """One content-free audit record per logical AI operation.

    The record holds only safe identifiers and aggregate figures: the
    Account, the feature, the consent-policy identifier, the safe outcome
    category, the actual route/model, aggregate token usage, and aggregate
    cost. Source text, prompts, raw outputs, and extracted values are never
    recorded. Account deletion cascades the audits.
    """

    OUTCOME_SUCCESS = "success"

    class Meta:
        verbose_name = "AI operation audit"
        verbose_name_plural = "AI operation audits"

    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_operation_audits",
    )
    feature = models.CharField(max_length=64)
    consent_policy = models.CharField(max_length=64)
    outcome = models.CharField(max_length=32)
    model = models.CharField(max_length=200, blank=True)
    route = models.CharField(max_length=200, blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.account.email} {self.feature}: {self.outcome}"
