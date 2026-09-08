"""The public AI boundary.

Exactly two task-specific operations are exposed to domain workflows:
Candidate Profile fact extraction and job-posting fact extraction. Each
accepts the authenticated Account and text, validates and bounds the canonical
text locally, rechecks current consent immediately before transmission, is
admitted through the usage-bounds gate, and returns only a locally validated
typed result or raises an AIError carrying a fixed safe category.

Exactly one content-free audit is recorded per logical operation after all
attempts, with aggregate usage and cost. A success is not returned unless its
audit is persisted; a failed audit becomes the safe internal-error category.
"""

import decimal
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from apps.accounts.models import Account
from apps.ai import admission, conf, engine, prompts, schemas, telemetry, transport
from apps.ai.engine import Usage
from apps.ai.errors import (
    CONFIGURATION_ERROR,
    CONSENT_REQUIRED,
    INTERNAL_ERROR,
    INVALID_INPUT,
    AIError,
)
from apps.ai.models import AIOperationAudit
from apps.ai.services import has_current_consent
from apps.documents.canonicalization import TextOverBudget, canonicalize

__all__ = ["extract_candidate_profile", "extract_job_posting"]

T = TypeVar("T")

# A deliberately conservative token estimate for the local pre-check. Some
# tokenizers approach one token per character, so this under-estimates on
# purpose; the provider's typed context-length error remains authoritative.
_CHARS_PER_TOKEN = 2


@dataclass(frozen=True)
class _FeatureSpec[T]:
    feature: str
    schema: Mapping[str, Any]
    schema_name: str
    system_prompt: str
    max_text_code_points: int
    parse: Callable[[object, str], T]


_PROFILE_SPEC = _FeatureSpec(
    feature=conf.FEATURE_CANDIDATE_PROFILE,
    schema=schemas.PROFILE_SCHEMA,
    schema_name=schemas.PROFILE_SCHEMA_NAME,
    system_prompt=prompts.profile_system_prompt(),
    max_text_code_points=100_000,
    parse=schemas.parse_profile_result,
)

_POSTING_SPEC = _FeatureSpec(
    feature=conf.FEATURE_JOB_POSTING,
    schema=schemas.POSTING_SCHEMA,
    schema_name=schemas.POSTING_SCHEMA_NAME,
    system_prompt=prompts.posting_system_prompt(),
    max_text_code_points=50_000,
    parse=schemas.parse_posting_result,
)


class _InvalidInput(Exception):
    """The text is empty, over its bound, or over the configured input limit."""


def extract_candidate_profile(account: Account, text: str) -> schemas.CandidateProfileExtraction:
    return _extract(account, text, _PROFILE_SPEC)


def extract_job_posting(account: Account, text: str) -> schemas.JobPostingExtraction:
    return _extract(account, text, _POSTING_SPEC)


def _extract[T](account: Account, raw_text: str, spec: _FeatureSpec[T]) -> T:
    policy = conf.current_policy()
    usage = Usage.zero()
    started = time.monotonic()
    try:
        config = conf.feature_config(spec.feature)
    except conf.ConfigurationError:
        _write_audit(account, spec.feature, policy, CONFIGURATION_ERROR, "", "", usage)
        raise AIError(CONFIGURATION_ERROR) from None

    try:
        canonical = _canonical_text(raw_text, spec, config)
    except _InvalidInput:
        _write_audit(account, spec.feature, policy, INVALID_INPUT, config.model, "", usage)
        raise AIError(INVALID_INPUT) from None

    # The last consent check before anything is transmitted.
    if not has_current_consent(account):
        _write_audit(account, spec.feature, policy, CONSENT_REQUIRED, config.model, "", usage)
        raise AIError(CONSENT_REQUIRED) from None

    # The usage-bounds gate: switches, in-flight, rolling windows, and budget.
    try:
        reservation = admission.admit(account, config)
    except AIError as rejection:
        _write_audit(account, spec.feature, policy, rejection.category, config.model, "", usage)
        raise

    audit_persisted = False
    try:
        try:
            payload = transport.build_payload(
                model=config.model,
                system_prompt=spec.system_prompt,
                user_text=prompts.user_content(canonical),
                schema_name=spec.schema_name,
                schema=spec.schema,
                max_output_tokens=config.max_output_tokens,
                price_ceiling=config.price_ceiling,
            )
            outcome = engine.run_extraction(
                config, payload=payload, validate=lambda decoded: spec.parse(decoded, canonical)
            )
        except Exception:
            # The engine and payload construction are total at the boundary.
            outcome = None
        if outcome is not None and outcome.ok and outcome.extraction is not None:
            category = AIOperationAudit.OUTCOME_SUCCESS
            observed_model = outcome.usage.model or config.model
            observed_route = outcome.usage.route
            observed_usage = outcome.usage
            extraction: T | None = outcome.extraction
        else:
            category = outcome.category or INTERNAL_ERROR if outcome is not None else INTERNAL_ERROR
            observed_model = (outcome.usage.model if outcome is not None else "") or config.model
            observed_route = outcome.usage.route if outcome is not None else ""
            observed_usage = outcome.usage if outcome is not None else usage
            extraction = None
        _write_audit(
            account,
            spec.feature,
            policy,
            category,
            observed_model,
            observed_route,
            observed_usage,
        )
        audit_persisted = True
        _emit_completed(
            account,
            spec.feature,
            category,
            attempts=outcome.attempts if outcome is not None else 0,
            started=started,
            usage=observed_usage,
        )
        if extraction is not None:
            return extraction
        raise AIError(category) from None
    finally:
        if audit_persisted:
            admission.reconcile(reservation)


def _emit_completed(
    account: Account,
    feature: str,
    outcome: str,
    *,
    attempts: int,
    started: float,
    usage: Usage,
) -> None:
    telemetry.log_event(
        "ai_operation_completed",
        account_id=account.pk,
        feature=feature,
        outcome=outcome,
        attempts=attempts,
        duration_ms=int((time.monotonic() - started) * 1000),
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost=str(usage.cost),
        version=telemetry.AI_TELEMETRY_VERSION,
    )


def _canonical_text(raw_text: str, spec: _FeatureSpec[Any], config: conf.FeatureConfig) -> str:
    text = raw_text if isinstance(raw_text, str) else ""
    try:
        canonical = canonicalize(text, max_code_points=spec.max_text_code_points)
    except TextOverBudget:
        raise _InvalidInput from None
    if not canonical:
        raise _InvalidInput
    if _estimate_tokens(len(canonical)) > config.max_input_tokens:
        raise _InvalidInput
    return canonical


def _estimate_tokens(code_points: int) -> int:
    return -(-code_points // _CHARS_PER_TOKEN)


def _write_audit(
    account: Account,
    feature: str,
    policy: str,
    outcome: str,
    model: str,
    route: str,
    usage: Usage,
) -> None:
    try:
        AIOperationAudit.objects.create(
            account=account,
            feature=feature,
            consent_policy=policy,
            outcome=outcome,
            model=model,
            route=route,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost=decimal.Decimal(usage.cost),
        )
    except Exception:
        # Audit persistence is mandatory: successes fail closed and failures
        # surface as the safe internal-error category.
        raise AIError(INTERNAL_ERROR) from None
