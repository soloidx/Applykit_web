from allauth.account.models import EmailAddress
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "core/home.html")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    if not EmailAddress.objects.filter(user=request.user, verified=True).exists():
        logout(request)
        return redirect("account_login")
    return render(request, "core/dashboard.html")
