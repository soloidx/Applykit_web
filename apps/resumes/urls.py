from django.urls import path

from apps.resumes.views import resume_detail, resume_save

urlpatterns = [
    path("applications/<int:application_id>/resume/", resume_detail, name="resume_detail"),
    path("applications/<int:application_id>/resume/save/", resume_save, name="resume_save"),
]
