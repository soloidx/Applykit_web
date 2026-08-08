from django.urls import path

from apps.campaigns.views import campaign_archive, campaign_create

urlpatterns = [
    path("campaigns/new/", campaign_create, name="campaign_create"),
    path(
        "campaigns/<int:campaign_id>/archive/",
        campaign_archive,
        name="campaign_archive",
    ),
]
