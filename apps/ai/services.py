"""AI-owned consent preference operations.

Absence of a consent preference means not accepted. Acceptance records the
configured global consent-policy identifier; changing that identifier makes
earlier acceptance invalid until the candidate renews consent. Declining and
withdrawing have the same effect: the preference stops counting as accepted
and accepted domain data stays under ordinary controls.
"""

import enum

from apps.accounts.models import Account
from apps.ai.conf import current_policy
from apps.ai.models import ConsentPreference

__all__ = [
    "ConsentStatus",
    "accept_consent",
    "consent_status",
    "decline_consent",
    "has_current_consent",
    "withdraw_consent",
]


class ConsentStatus(enum.StrEnum):
    CURRENT = "current"
    RENEWAL_REQUIRED = "renewal_required"
    NOT_ACCEPTED = "not_accepted"


def consent_status(account: Account) -> ConsentStatus:
    preference = ConsentPreference.objects.filter(account=account).first()
    if preference is None or not preference.accepted:
        return ConsentStatus.NOT_ACCEPTED
    if preference.policy != current_policy():
        return ConsentStatus.RENEWAL_REQUIRED
    return ConsentStatus.CURRENT


def has_current_consent(account: Account) -> bool:
    return consent_status(account) is ConsentStatus.CURRENT


def accept_consent(*, account: Account) -> None:
    ConsentPreference.objects.update_or_create(
        account=account,
        defaults={"accepted": True, "policy": current_policy()},
    )


def decline_consent(*, account: Account) -> None:
    _record_refusal(account)


def withdraw_consent(*, account: Account) -> None:
    _record_refusal(account)


def _record_refusal(account: Account) -> None:
    ConsentPreference.objects.update_or_create(
        account=account,
        defaults={"accepted": False, "policy": ""},
    )
