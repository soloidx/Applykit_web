from django.db import models


class CoverLetter(models.Model):
    application = models.OneToOneField(
        "applications.JobApplication",
        on_delete=models.CASCADE,
        related_name="cover_letter",
    )
    body_html = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
