from typing import cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.applications.forms import JobApplicationCreateForm, JobApplicationEditForm
from apps.applications.models import Company, JobApplication
from apps.applications.services import create_or_reuse_company
from apps.campaigns.models import Campaign


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _redirect_or_htmx_redirect(request: HttpRequest, application: JobApplication) -> HttpResponse:
    destination = reverse("application_edit", args=[application.pk])
    if _is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = destination
        return response
    return redirect(destination)


@login_required
@verified_account_required
def application_create(request: HttpRequest) -> HttpResponse:
    account = cast(Account, request.user)
    campaign = get_object_or_404(Campaign, account=account, status=Campaign.Status.ACTIVE)
    if request.method == "GET":
        form = JobApplicationCreateForm()
        query = request.GET.get("q", "").strip()
        if query:
            company_field = cast(forms.ModelChoiceField, form.fields["company"])
            company_field.queryset = Company.objects.filter(
                Q(name__icontains=query)
                | Q(canonical_domain__icontains=query)
                | Q(domain_aliases__domain__icontains=query)
            ).order_by("name")
        return render(request, "applications/form.html", {"form": form, "company_query": query})
    if request.method != "POST":
        return HttpResponse(status=405)

    form = JobApplicationCreateForm(request.POST)
    if form.is_valid():
        company = form.cleaned_data["company"]
        reused = False
        if company is None:
            try:
                company, created = create_or_reuse_company(
                    str(form.cleaned_data["company_name"]),
                    str(form.cleaned_data["website"] or "") or None,
                )
            except ValidationError as error:
                form.add_error("website", error)
            else:
                reused = not created
        if company is not None and not form.errors:
            application = JobApplication.objects.create(
                account=account,
                campaign=campaign,
                company=company,
                role_title=str(form.cleaned_data["role_title"]),
                job_description=str(form.cleaned_data["job_description"]),
                posting_url=str(form.cleaned_data["posting_url"]),
                location=str(form.cleaned_data["location"]),
                compensation=str(form.cleaned_data["compensation"]),
                source=str(form.cleaned_data["source"]),
                private_notes=str(form.cleaned_data["private_notes"]),
            )
            if reused:
                messages.info(request, "An existing company matched that website and was reused.")
            return _redirect_or_htmx_redirect(request, application)
    return render(request, "applications/form.html", {"form": form})


@login_required
@verified_account_required
def application_edit(request: HttpRequest, application_id: int) -> HttpResponse:
    account = cast(Account, request.user)
    application = get_object_or_404(
        JobApplication,
        pk=application_id,
        account=account,
        stage=JobApplication.Stage.DRAFT,
    )
    if request.method == "GET":
        return render(
            request,
            "applications/form.html",
            {"form": JobApplicationEditForm(instance=application), "application": application},
        )
    if request.method != "POST":
        return HttpResponse(status=405)

    form = JobApplicationEditForm(request.POST, instance=application)
    if form.is_valid():
        form.save()
        return _redirect_or_htmx_redirect(request, application)
    return render(request, "applications/form.html", {"form": form, "application": application})
