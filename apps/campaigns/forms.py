from django import forms

from apps.campaigns.models import Campaign


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["weekly_target", "monthly_target"]
