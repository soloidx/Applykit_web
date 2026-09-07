from django.urls import path

from apps.ai.views import ai_consent

urlpatterns = [
    path("ai/consent/", ai_consent, name="ai_consent"),
]
