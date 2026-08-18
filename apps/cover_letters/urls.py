from django.urls import path

from apps.cover_letters.views import cover_letter_delete, cover_letter_detail, cover_letter_save

urlpatterns = [
    path(
        "applications/<int:application_id>/cover-letter/",
        cover_letter_detail,
        name="cover_letter_detail",
    ),
    path(
        "applications/<int:application_id>/cover-letter/save/",
        cover_letter_save,
        name="cover_letter_save",
    ),
    path(
        "applications/<int:application_id>/cover-letter/delete/",
        cover_letter_delete,
        name="cover_letter_delete",
    ),
]
