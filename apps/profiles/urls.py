from django.urls import path

from apps.profiles.views import (
    experience_create,
    experience_delete,
    experience_edit,
    experience_reorder,
    highlight_create,
    highlight_delete,
    highlight_edit,
    highlight_reorder,
    profile,
)

urlpatterns = [
    path("profile/", profile, name="profile"),
    path("profile/experience/new/", experience_create, name="experience_create"),
    path(
        "profile/experience/<int:experience_id>/edit/",
        experience_edit,
        name="experience_edit",
    ),
    path(
        "profile/experience/<int:experience_id>/delete/",
        experience_delete,
        name="experience_delete",
    ),
    path(
        "profile/experience/<int:experience_id>/reorder/",
        experience_reorder,
        name="experience_reorder",
    ),
    path(
        "profile/experience/<int:experience_id>/highlights/new/",
        highlight_create,
        name="highlight_create",
    ),
    path(
        "profile/experience/<int:experience_id>/highlights/<int:highlight_id>/edit/",
        highlight_edit,
        name="highlight_edit",
    ),
    path(
        "profile/experience/<int:experience_id>/highlights/<int:highlight_id>/delete/",
        highlight_delete,
        name="highlight_delete",
    ),
    path(
        "profile/experience/<int:experience_id>/highlights/<int:highlight_id>/reorder/",
        highlight_reorder,
        name="highlight_reorder",
    ),
]
