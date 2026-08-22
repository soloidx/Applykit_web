from django.contrib import admin
from django.urls import include, path

from apps.core.views import dashboard, home, import_journeys_prototype

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.applications.urls")),
    path("", include("apps.profiles.urls")),
    path("", include("apps.campaigns.urls")),
    path("", include("apps.resumes.urls")),
    path("", include("apps.cover_letters.urls")),
    path("dashboard/", dashboard, name="dashboard"),
    path(
        "prototype/import-journeys/",
        import_journeys_prototype,
        name="import_journeys_prototype",
    ),
    path("", home, name="home"),
]
