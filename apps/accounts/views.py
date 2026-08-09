from typing import cast

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.accounts.services import delete_account


def _redirect_after_deletion(request: HttpRequest) -> HttpResponse:
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("home")
        return response
    return redirect("home")


@login_required
@verified_account_required
def account_delete(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    if request.method == "GET":
        return render(request, "account/delete.html")
    if request.method != "POST":
        return HttpResponse(status=405)
    if request.POST.get("cancel"):
        return redirect("profile")
    if request.POST.get("confirm"):
        delete_account(account=account)
        logout(request)
        return _redirect_after_deletion(request)
    return render(request, "account/delete.html", status=400)
