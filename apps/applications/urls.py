from django.urls import path

from apps.applications.views import application_create, application_detail, application_edit

urlpatterns = [
    path("applications/new/", application_create, name="application_create"),
    path("applications/<int:application_id>/", application_detail, name="application_detail"),
    path("applications/<int:application_id>/edit/", application_edit, name="application_edit"),
]
