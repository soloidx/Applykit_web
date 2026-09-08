"""Data-access output for one Account's AI records.

The output includes the current consent state and every content-free AI
audit. Source content, prompts, and raw provider outputs are never stored, so
the output explains why they cannot be included.
"""

import decimal
from dataclasses import dataclass
from datetime import datetime

from apps.accounts.models import Account
from apps.ai.conf import current_policy
from apps.ai.models import AIOperationAudit
from apps.ai.services import ConsentStatus, consent_status

__all__ = ["CONTENT_EXPLANATION", "AccountAIData", "AuditSummary", "account_ai_data"]

CONTENT_EXPLANATION = (
    "AI operations process only the bounded extracted text from your sources. "
    "ApplyKit never stores your source content, prompts, or raw AI output, so "
    "those are not available in this data access."
)


@dataclass(frozen=True)
class AuditSummary:
    """One content-free audit record of a logical AI operation."""

    feature: str
    consent_policy: str
    outcome: str
    model: str
    route: str
    prompt_tokens: int
    completion_tokens: int
    cost: decimal.Decimal
    created_at: datetime


@dataclass(frozen=True)
class AccountAIData:
    consent: ConsentStatus
    consent_policy: str
    audits: tuple[AuditSummary, ...]
    content_explanation: str


def account_ai_data(account: Account) -> AccountAIData:
    rows = (
        AIOperationAudit.objects.filter(account=account)
        .order_by("-created_at", "-pk")
        .values(
            "feature",
            "consent_policy",
            "outcome",
            "model",
            "route",
            "prompt_tokens",
            "completion_tokens",
            "cost",
            "created_at",
        )
    )
    return AccountAIData(
        consent=consent_status(account),
        consent_policy=current_policy(),
        audits=tuple(
            AuditSummary(
                feature=row["feature"],
                consent_policy=row["consent_policy"],
                outcome=row["outcome"],
                model=row["model"],
                route=row["route"],
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                cost=row["cost"],
                created_at=row["created_at"],
            )
            for row in rows
        ),
        content_explanation=CONTENT_EXPLANATION,
    )
