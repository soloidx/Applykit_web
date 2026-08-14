from __future__ import annotations

from typing import cast

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.profiles.forms import (
    CandidateProfileForm,
    EducationForm,
    ExperienceForm,
    HighlightForm,
    LanguageForm,
    ProjectForm,
    ProjectSkillForm,
    SkillForm,
)
from apps.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    Highlight,
    Language,
    Project,
    ProjectSkill,
    Skill,
)
from apps.skills.services import resolve_skill_label


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
    education_form: EducationForm | None = None,
    education_form_action: str | None = None,
    project_form: ProjectForm | None = None,
    project_form_action: str | None = None,
    project_skill_form: ProjectSkillForm | None = None,
    project_skill_project: Project | None = None,
    skill_form: SkillForm | None = None,
    skill_form_action: str | None = None,
    language_form: LanguageForm | None = None,
    language_form_action: str | None = None,
) -> dict[str, object]:
    return {
        "candidate_profile": candidate_profile,
        "experiences": candidate_profile.experiences.prefetch_related("highlights"),
        "experience_form": experience_form if experience_form is not None else ExperienceForm(),
        "experience_form_action": experience_form_action or reverse("experience_create"),
        "highlight_form": highlight_form,
        "highlight_experience": highlight_experience,
        "highlight_form_action": highlight_form_action,
        "educations": candidate_profile.educations.all(),
        "education_form": education_form if education_form is not None else EducationForm(),
        "education_form_action": education_form_action or reverse("education_create"),
        "projects": candidate_profile.projects.all(),
        "project_form": project_form if project_form is not None else ProjectForm(),
        "project_form_action": project_form_action or reverse("project_create"),
        "project_skill_form": project_skill_form
        if project_skill_form is not None
        else ProjectSkillForm(),
        "project_skill_project": project_skill_project,
        "project": project_skill_project,
        "skills": candidate_profile.skills.all(),
        "skill_form": skill_form
        if skill_form is not None
        else SkillForm(profile=candidate_profile),
        "skill_form_action": skill_form_action or reverse("skill_create"),
        "languages": candidate_profile.languages.all(),
        "language_form": language_form
        if language_form is not None
        else LanguageForm(profile=candidate_profile),
        "language_form_action": language_form_action or reverse("language_create"),
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


def _education_for_account(account: Account, education_id: int) -> Education:
    return get_object_or_404(Education, pk=education_id, profile__account=account)


def _project_for_account(account: Account, project_id: int) -> Project:
    return get_object_or_404(Project, pk=project_id, profile__account=account)


def _project_skill_for_account(account: Account, project_skill_id: int) -> ProjectSkill:
    return get_object_or_404(
        ProjectSkill,
        pk=project_skill_id,
        project__profile__account=account,
    )


def _skill_for_account(account: Account, skill_id: int) -> Skill:
    return get_object_or_404(Skill, pk=skill_id, profile__account=account)


def _language_for_account(account: Account, language_id: int) -> Language:
    return get_object_or_404(Language, pk=language_id, profile__account=account)


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


def _normalize_education_positions(profile: CandidateProfile) -> None:
    for position, education in enumerate(profile.educations.order_by("position", "id")):
        if education.position != position:
            Education.objects.filter(pk=education.pk).update(position=position)


def _normalize_project_positions(profile: CandidateProfile) -> None:
    for position, project in enumerate(profile.projects.order_by("position", "id")):
        if project.position != position:
            Project.objects.filter(pk=project.pk).update(position=position)


def _normalize_project_skill_positions(project: Project) -> None:
    for position, project_skill in enumerate(project.project_skills.order_by("position", "id")):
        if project_skill.position != position:
            ProjectSkill.objects.filter(pk=project_skill.pk).update(position=position)


def _normalize_skill_positions(profile: CandidateProfile) -> None:
    for position, skill in enumerate(profile.skills.order_by("position", "id")):
        if skill.position != position:
            Skill.objects.filter(pk=skill.pk).update(position=position)


def _normalize_language_positions(profile: CandidateProfile) -> None:
    for position, language in enumerate(profile.languages.order_by("position", "id")):
        if language.position != position:
            Language.objects.filter(pk=language.pk).update(position=position)


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
def education_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    candidate_profile = _profile_for_account(account)
    form = EducationForm(request.POST or None)
    action = reverse("education_create")
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked_profile = CandidateProfile.objects.select_for_update().get(
                pk=candidate_profile.pk
            )
            education = form.save(commit=False)
            education.profile = locked_profile
            education.position = locked_profile.educations.count()
            education.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        candidate_profile,
        education_form=form,
        education_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_education_form.html", context)
    context["form"] = CandidateProfileForm(instance=candidate_profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def education_edit(request: HttpRequest, education_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    education = _education_for_account(account, education_id)
    form = EducationForm(request.POST or None, instance=education)
    action = reverse("education_edit", args=[education.pk])
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        education.profile,
        education_form=form,
        education_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_education_form.html", context)
    context["form"] = CandidateProfileForm(instance=education.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def education_delete(request: HttpRequest, education_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    education = _education_for_account(account, education_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=education.profile_id)
        education.delete()
        _normalize_education_positions(profile)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def education_reorder(request: HttpRequest, education_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    education = _education_for_account(account, education_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=education.profile_id)
        educations = list(profile.educations.order_by("position", "id"))
        index = educations.index(education)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(educations):
            educations[index], educations[swap_index] = educations[swap_index], educations[index]
            for position, item in enumerate(educations):
                Education.objects.filter(pk=item.pk).update(position=position)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def project_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    candidate_profile = _profile_for_account(account)
    form = ProjectForm(request.POST or None)
    action = reverse("project_create")
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked_profile = CandidateProfile.objects.select_for_update().get(
                pk=candidate_profile.pk
            )
            project = form.save(commit=False)
            project.profile = locked_profile
            project.position = locked_profile.projects.count()
            project.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        candidate_profile,
        project_form=form,
        project_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_project_form.html", context)
    context["form"] = CandidateProfileForm(instance=candidate_profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def project_edit(request: HttpRequest, project_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    project = _project_for_account(account, project_id)
    form = ProjectForm(request.POST or None, instance=project)
    action = reverse("project_edit", args=[project.pk])
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        project.profile,
        project_form=form,
        project_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_project_form.html", context)
    context["form"] = CandidateProfileForm(instance=project.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def project_delete(request: HttpRequest, project_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    project = _project_for_account(account, project_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=project.profile_id)
        project.delete()
        _normalize_project_positions(profile)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def project_reorder(request: HttpRequest, project_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    project = _project_for_account(account, project_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=project.profile_id)
        projects = list(profile.projects.order_by("position", "id"))
        index = projects.index(project)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(projects):
            projects[index], projects[swap_index] = projects[swap_index], projects[index]
            for position, item in enumerate(projects):
                Project.objects.filter(pk=item.pk).update(position=position)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def project_skill_create(request: HttpRequest, project_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    project = _project_for_account(account, project_id)
    form = ProjectSkillForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_project = Project.objects.select_for_update().get(pk=project.pk)
                concept, _ = resolve_skill_label(form.cleaned_data["label"])
                if ProjectSkill.objects.filter(
                    project=locked_project,
                    concept=concept,
                ).exists():
                    form.add_error("label", "This skill is already used in this project.")
                else:
                    ProjectSkill.objects.create(
                        project=locked_project,
                        concept=concept,
                        label=form.cleaned_data["label"],
                        position=locked_project.project_skills.count(),
                    )
        except IntegrityError:
            form.add_error("label", "This skill is already used in this project.")
        if not form.errors:
            return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        project.profile,
        project_skill_form=form,
        project_skill_project=project,
    )
    if _is_htmx(request):
        return render(request, "profiles/_project_skill_form.html", context)
    context["form"] = CandidateProfileForm(instance=project.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def project_skill_delete(request: HttpRequest, project_skill_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    project_skill = _project_skill_for_account(account, project_skill_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_skill.project_id)
        project_skill.delete()
        _normalize_project_skill_positions(project)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def project_skill_reorder(request: HttpRequest, project_skill_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    project_skill = _project_skill_for_account(account, project_skill_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project_skill.project_id)
        project_skills = list(project.project_skills.order_by("position", "id"))
        index = project_skills.index(project_skill)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(project_skills):
            project_skills[index], project_skills[swap_index] = (
                project_skills[swap_index],
                project_skills[index],
            )
            for position, item in enumerate(project_skills):
                ProjectSkill.objects.filter(pk=item.pk).update(position=position)
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


@login_required
@verified_account_required
def skill_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    candidate_profile = _profile_for_account(account)
    form = SkillForm(request.POST or None, profile=candidate_profile)
    action = reverse("skill_create")
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_profile = CandidateProfile.objects.select_for_update().get(
                    pk=candidate_profile.pk
                )
                normalized_name = form.cleaned_data["name"].casefold()
                if Skill.objects.filter(
                    profile=locked_profile,
                    normalized_name=normalized_name,
                ).exists():
                    form.add_error("name", "This skill is already in your profile.")
                else:
                    skill = form.save(commit=False)
                    skill.profile = locked_profile
                    skill.position = locked_profile.skills.count()
                    skill.save()
        except IntegrityError:
            form.add_error("name", "This skill is already in your profile.")
        if not form.errors:
            return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        candidate_profile,
        skill_form=form,
        skill_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_skill_form.html", context)
    context["form"] = CandidateProfileForm(instance=candidate_profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def skill_delete(request: HttpRequest, skill_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    skill = _skill_for_account(account, skill_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=skill.profile_id)
        skill.delete()
        _normalize_skill_positions(profile)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def skill_reorder(request: HttpRequest, skill_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    skill = _skill_for_account(account, skill_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=skill.profile_id)
        skills = list(profile.skills.order_by("position", "id"))
        index = skills.index(skill)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(skills):
            skills[index], skills[swap_index] = skills[swap_index], skills[index]
            for position, item in enumerate(skills):
                Skill.objects.filter(pk=item.pk).update(position=position)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def language_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    candidate_profile = _profile_for_account(account)
    form = LanguageForm(request.POST or None, profile=candidate_profile)
    action = reverse("language_create")
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked_profile = CandidateProfile.objects.select_for_update().get(
                    pk=candidate_profile.pk
                )
                normalized_name = form.cleaned_data["name"].casefold()
                if Language.objects.filter(
                    profile=locked_profile,
                    normalized_name=normalized_name,
                ).exists():
                    form.add_error("name", "This language is already in your profile.")
                else:
                    language = form.save(commit=False)
                    language.profile = locked_profile
                    language.position = locked_profile.languages.count()
                    language.save()
        except IntegrityError:
            form.add_error("name", "This language is already in your profile.")
        if not form.errors:
            return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        candidate_profile,
        language_form=form,
        language_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_language_form.html", context)
    context["form"] = CandidateProfileForm(instance=candidate_profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def language_edit(request: HttpRequest, language_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    language = _language_for_account(account, language_id)
    form = LanguageForm(request.POST or None, instance=language, profile=language.profile)
    action = reverse("language_edit", args=[language.pk])
    if request.method == "POST" and form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(request)

    context = _profile_context(
        language.profile,
        language_form=form,
        language_form_action=action,
    )
    if _is_htmx(request):
        return render(request, "profiles/_language_form.html", context)
    context["form"] = CandidateProfileForm(instance=language.profile)
    return render(request, "profiles/profile.html", context)


@login_required
@verified_account_required
def language_delete(request: HttpRequest, language_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    language = _language_for_account(account, language_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=language.profile_id)
        language.delete()
        _normalize_language_positions(profile)
    return _redirect_or_htmx_redirect(request)


@login_required
@verified_account_required
def language_reorder(request: HttpRequest, language_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    language = _language_for_account(account, language_id)
    if request.method != "POST":
        return HttpResponse(status=405)
    direction = request.POST.get("direction")
    if direction not in {"up", "down"}:
        return HttpResponse("Direction must be up or down.", status=400)

    with transaction.atomic():
        profile = CandidateProfile.objects.select_for_update().get(pk=language.profile_id)
        languages = list(profile.languages.order_by("position", "id"))
        index = languages.index(language)
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(languages):
            languages[index], languages[swap_index] = languages[swap_index], languages[index]
            for position, item in enumerate(languages):
                Language.objects.filter(pk=item.pk).update(position=position)
    return _redirect_or_htmx_redirect(request)
