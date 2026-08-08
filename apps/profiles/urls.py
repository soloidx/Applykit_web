from django.urls import path

from apps.profiles.views import (
    education_create,
    education_delete,
    education_edit,
    education_reorder,
    experience_create,
    experience_delete,
    experience_edit,
    experience_reorder,
    highlight_create,
    highlight_delete,
    highlight_edit,
    highlight_reorder,
    profile,
    project_create,
    project_delete,
    project_edit,
    project_reorder,
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
    path("profile/education/new/", education_create, name="education_create"),
    path(
        "profile/education/<int:education_id>/edit/",
        education_edit,
        name="education_edit",
    ),
    path(
        "profile/education/<int:education_id>/delete/",
        education_delete,
        name="education_delete",
    ),
    path(
        "profile/education/<int:education_id>/reorder/",
        education_reorder,
        name="education_reorder",
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
    path("profile/projects/new/", project_create, name="project_create"),
    path(
        "profile/projects/<int:project_id>/edit/",
        project_edit,
        name="project_edit",
    ),
    path(
        "profile/projects/<int:project_id>/delete/",
        project_delete,
        name="project_delete",
    ),
    path(
        "profile/projects/<int:project_id>/reorder/",
        project_reorder,
        name="project_reorder",
    ),
]
