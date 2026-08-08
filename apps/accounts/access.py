from collections.abc import Callable
from functools import wraps
from typing import cast

from allauth.account.models import EmailAddress
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from apps.accounts.models import Account


def has_verified_email(account: Account) -> bool:
    return EmailAddress.objects.filter(user=account, verified=True).exists()


def verified_account_required[View: Callable[..., HttpResponse]](view: View) -> View:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not has_verified_email(cast(Account, request.user)):
            logout(request)
            return redirect("account_login")
        return view(request, *args, **kwargs)

    return cast(View, wrapped)
