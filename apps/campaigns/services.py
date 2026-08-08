from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import Account
from apps.campaigns.models import Campaign
from apps.profiles.models import CandidateProfile


def activate_campaign(account: Account, weekly_target: int, monthly_target: int) -> Campaign:
    if weekly_target < 1 or monthly_target < 1:
        raise ValidationError("Campaign targets must be positive integers.")

    with transaction.atomic():
        Account.objects.select_for_update().get(pk=account.pk)
        try:
            profile = CandidateProfile.objects.get(account=account)
        except CandidateProfile.DoesNotExist as error:
            raise ValidationError(
                "Complete your Candidate Profile before starting a campaign."
            ) from error
        if not profile.has_minimum_details:
            raise ValidationError("Complete your Candidate Profile before starting a campaign.")
        return Campaign.objects.create(
            account=account,
            weekly_target=weekly_target,
            monthly_target=monthly_target,
            timezone=profile.timezone,
        )
