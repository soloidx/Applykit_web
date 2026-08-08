from __future__ import annotations

from typing import cast

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.profiles.forms import CandidateProfileForm, ExperienceForm, HighlightForm
from apps.profiles.models import CandidateProfile, Experience, Highlight


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _profile_context(
    candidate_profile: CandidateProfile,
    *,
    experience_form: ExperienceForm | None = None,
    experience_form_action: str | None = None,
    highlight_form: HighlightForm | None = None,
    highlight_experience: Experience | None = None,
    highlight_form_action: str | None = None,
) -> dict[str, object]:
    return {
        "candidate_profile": candidate_profile,
        "experiences": candidate_profile.experiences.prefetch_related("highlights"),
        "experience_form": experience_form if experience_form is not None else ExperienceForm(),
        "experience_form_action": experience_form_action or reverse("experience_create"),
        "highlight_form": highlight_form,
        "highlight_experience": highlight_experience,
        "highlight_form_action": highlight_form_action,
    }


def _profile_for_account(account: Account) -> CandidateProfile:
    return get_object_or_404(CandidateProfile, account=account)


def _experience_for_account(account: Account, experience_id: int) -> Experience:
    return get_object_or_404(Experience, pk=experience_id, profile__account=account)


def _highlight_for_account(
    account: Account, experience_id: int, highlight_id: int
) -> tuple[Experience, Highlight]:
    experience = _experience_for_account(account, experience_id)
    highlight = get_object_or_404(Highlight, pk=highlight_id, experience=experience)
    return experience, highlight


def _redirect_or_htmx_redirect(request: HttpRequest) -> HttpResponse:
    if _is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("profile")
        return response
    return redirect("profile")


def _normalize_experience_positions(profile: CandidateProfile) -> None:
    for position, experience in enumerate(profile.experiences.order_by("position", "id")):
        if experience.position != position:
            Experience.objects.filter(pk=experience.pk).update(position=position)


def _normalize_highlight_positions(experience: Experience) -> None:
    for position, highlight in enumerate(experience.highlights.order_by("position", "id")):
        if highlight.position != position:
            Highlight.objects.filter(pk=highlight.pk).update(position=position)


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

    template = "profiles/_form.html" if _is_htmx(request) else "profiles/profile.html"
    context: dict[str, object] = {"form": form}
    if candidate_profile is not None and template == "profiles/profile.html":
        context.update(_profile_context(candidate_profile))
    return render(request, template, context)


@login_required
@verified_account_required
def experience_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    candidate_profile = _profile_for_account(account)
    form = ExperienceForm(request.POST or None)
    action = reverse("experience_create")
    if request.method == "POST" and form.is_valid():
        experience = form.save(commit=False)
        experience.profile = candidate_profile
        experience.position = candidate_profile.experiences.count()
        experience.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        candidate_profile,
        experience_form=form,
        experience_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_experience_form.html", context)
    context["form"] = CandidateProfileForm(instance=candidate_profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def experience_edit(request: HttpRequest, experience_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience = _experience_for_account(account, experience_id)
    form = ExperienceForm(request.POST or None, instance=experience)
    action = reverse("experience_edit", args=[experience.pk])
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        experience.profile,
        experience_form=form,
        experience_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_experience_form.html", context)
    context["form"] = CandidateProfileForm(instance=experience.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def experience_delete(request: HttpRequest, experience_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience = _experience_for_account(account, experience_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    profile = experience.profile
    with transaction.atomic():
        experience.delete()
        _normalize_experience_positions(profile)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def experience_reorder(request: HttpRequest, experience_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience = _experience_for_account(account, experience_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        experiences = list(experience.profile.experiences.order_by("position", "id"))
        index = experiences.index(experience)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(experiences):
            experiences[index], experiences[swap_index] = (
                experiences[swap_index],
                experiences[index],
            )
            for position, item in enumerate(experiences):
                Experience.objects.filter(pk=item.pk).update(position=position)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def highlight_create(request: HttpRequest, experience_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience = _experience_for_account(account, experience_id)
    form = HighlightForm(request.POST or None)
    action = reverse("highlight_create", args=[experience.pk])
    if request.method == "POST" and form.is_valid():
        highlight = form.save(commit=False)
        highlight.experience = experience
        highlight.position = experience.highlights.count()
        highlight.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        experience.profile,
        highlight_form=form,
        highlight_experience=experience,
        highlight_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_highlight_form.html", context)
    context["form"] = CandidateProfileForm(instance=experience.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def highlight_edit(request: HttpRequest, experience_id: int, highlight_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience, highlight = _highlight_for_account(account, experience_id, highlight_id)
    form = HighlightForm(request.POST or None, instance=highlight)
    action = reverse("highlight_edit", args=[experience.pk, highlight.pk])
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        experience.profile,
        highlight_form=form,
        highlight_experience=experience,
        highlight_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_highlight_form.html", context)
    context["form"] = CandidateProfileForm(instance=experience.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def highlight_delete(request: HttpRequest, experience_id: int, highlight_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience, highlight = _highlight_for_account(account, experience_id, highlight_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    with transaction.atomic():
        highlight.delete()
        _normalize_highlight_positions(experience)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def highlight_reorder(request: HttpRequest, experience_id: int, highlight_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    experience, highlight = _highlight_for_account(account, experience_id, highlight_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        highlights = list(experience.highlights.order_by("position", "id"))
        index = highlights.index(highlight)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(highlights):
            highlights[index], highlights[swap_index] = highlights[swap_index], highlights[index]
            for position, item in enumerate(highlights):
                Highlight.objects.filter(pk=item.pk).update(position=position)
    return _redirect_or_htmx_redirect(request)
