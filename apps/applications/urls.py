from django.urls import path

from apps.applications.views import (
    application_board,
    application_create,
    application_delete,
    application_detail,
    application_edit,
    recruitment_event_create,
    recruitment_event_edit,
)

urlpatterns = [
    path("applications/", application_board, name="application_board"),
    path("applications/new/", application_create, name="application_create"),
    path("applications/<int:application_id>/", application_detail, name="application_detail"),
    path("applications/<int:application_id>/edit/", application_edit, name="application_edit"),
    path(
        "applications/<int:application_id>/delete/",
        application_delete,
        name="application_delete",
    ),
    path(
        "applications/<int:application_id>/events/new/",
        recruitment_event_create,
        name="recruitment_event_create",
    ),
    path(
        "applications/<int:application_id>/events/<int:event_id>/edit/",
        recruitment_event_edit,
        name="recruitment_event_edit",
    ),
]
