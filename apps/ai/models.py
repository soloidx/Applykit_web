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
