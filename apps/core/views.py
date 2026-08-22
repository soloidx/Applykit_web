from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.campaigns.views import dashboard_context
from apps.profiles.access import minimum_profile_complete


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "core/home.html")


def import_journeys_prototype(request: HttpRequest) -> HttpResponse:
    return render(request, "core/import_journeys_prototype.html")


@login_required
@verified_account_required
def dashboard(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    if not minimum_profile_complete(account):
        return redirect("profile")
    return render(request, "core/dashboard.html", dashboard_context(account))
