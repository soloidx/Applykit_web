from django.contrib import admin
from django.urls import include, path

from apps.core.views import home

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("", home, name="home"),
]
