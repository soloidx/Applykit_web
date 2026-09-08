from django.urls import path

from apps.ai.views import ai_consent, ai_data_access

urlpatterns = [
    path("ai/consent/", ai_consent, name="ai_consent"),
    path("ai/data/", ai_data_access, name="ai_data_access"),
]
