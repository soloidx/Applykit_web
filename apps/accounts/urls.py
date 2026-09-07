from django.urls import path

from apps.accounts.views import account_delete, account_home

urlpatterns = [
    path("account/", account_home, name="account_home"),
    path("account/delete/", account_delete, name="account_delete"),
]
