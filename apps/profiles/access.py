from apps.accounts.models import Account
from apps.profiles.models import CandidateProfile


def minimum_profile_complete(account: Account) -> bool:
    profile = CandidateProfile.objects.filter(account=account).first()
    return profile is not None and profile.has_minimum_details
