from __future__ import annotations

from typing import cast

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.profiles.forms import CandidateProfileForm
from apps.profiles.models import CandidateProfile


@login_required
@verified_account_required
def profile(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    candidate_profile = CandidateProfile.objects.filter(account=account).first()
    if request.method == "POST":
        form = CandidateProfileForm(request.POST, instance=candidate_profile)
        if form.is_valid():
            candidate_profile = form.save(commit=False)
            candidate_profile.account = account
            candidate_profile.save()
            if request.headers.get("HX-Request") == "true":
                response = render(
                    request,
                    "profiles/_form.html",
                    {
                        "form": CandidateProfileForm(instance=candidate_profile),
                        "profile_saved": True,
                    },
                )
                response["HX-Redirect"] = reverse("dashboard")
                return response
            return redirect("dashboard")
    else:
        form = CandidateProfileForm(instance=candidate_profile)

    template = (
        "profiles/_form.html"
        if request.headers.get("HX-Request") == "true"
        else "profiles/profile.html"
    )
    return render(request, template, {"form": form})
