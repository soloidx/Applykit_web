from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.ai.data_access import account_ai_data
from apps.ai.services import accept_consent, consent_status, decline_consent, withdraw_consent


def _safe_next(request: HttpRequest) -> str:
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(candidate, allowed_hosts=None):
        return candidate
    return ""


@login_required
@verified_account_required
def ai_consent(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    if request.method == "GET":
        return render(
            request,
            "ai/consent.html",
            {"consent": consent_status(account), "next": _safe_next(request)},
        )
    if request.method != "POST":
        return HttpResponse(status=405)
    next_url = _safe_next(request) or reverse("dashboard")
    if request.POST.get("accept"):
        accept_consent(account=account)
        return redirect(next_url)
    if request.POST.get("decline"):
        decline_consent(account=account)
        return redirect(next_url)
    if request.POST.get("withdraw"):
        withdraw_consent(account=account)
        return redirect(next_url)
    return HttpResponse(status=400)


@login_required
@verified_account_required
def ai_data_access(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    if request.method != "GET":
        return HttpResponse(status=405)
    try:
        data = account_ai_data(account)
    except Exception:
        return HttpResponse(status=503)
    return render(request, "ai/data_access.html", {"data": data})
