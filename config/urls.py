from django.contrib import admin
from django.urls import include, path

from apps.core.views import dashboard, home

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.applications.urls")),
    path("", include("apps.profiles.urls")),
    path("", include("apps.campaigns.urls")),
    path("", include("apps.resumes.urls")),
    path("dashboard/", dashboard, name="dashboard"),
    path("", home, name="home"),
]
