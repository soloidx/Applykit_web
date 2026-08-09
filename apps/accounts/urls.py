from django.urls import path

from apps.accounts.views import account_delete

urlpatterns = [
    path("account/delete/", account_delete, name="account_delete"),
]
