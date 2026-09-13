"""Opaque, server-verifiable Candidate Profile version tokens.

A review draft is browser-only, so the save request must prove which profile
revision (including the absent-profile state used during onboarding) the
candidate reviewed. The token is signed with the application secret and bound
to the owning Account; the server re-reads the current state before writing and
rejects a stale token rather than force-replacing later edits.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core import signing

from apps.accounts.models import Account
from apps.profiles.models import CandidateProfile

__all__ = [
    "InvalidProfileVersion",
    "ProfileVersion",
    "profile_version_token",
    "read_profile_version",
]

_SALT = "profiles.candidate-profile-version"


class InvalidProfileVersion(Exception):
    """The submitted version token is missing, forged, or for another Account."""


@dataclass(frozen=True)
class ProfileVersion:
    account_id: int
    revision: str | None

    @property
    def is_absent(self) -> bool:
        return self.revision is None


def profile_version_token(account: Account, profile: CandidateProfile | None) -> str:
    revision = str(profile.revision) if profile is not None else None
    return signing.dumps({"account": account.pk, "revision": revision}, salt=_SALT)


def read_profile_version(token: str, account: Account) -> ProfileVersion:
    try:
        payload = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        raise InvalidProfileVersion from None
    if not isinstance(payload, dict) or payload.get("account") != account.pk:
        raise InvalidProfileVersion
    revision = payload.get("revision")
    if revision is not None and not isinstance(revision, str):
        raise InvalidProfileVersion
    return ProfileVersion(account_id=account.pk, revision=revision)
