from django.urls import path

from apps.profiles.views import profile

urlpatterns = [
    path("profile/", profile, name="profile"),
]
