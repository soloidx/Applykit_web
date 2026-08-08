from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render


def home(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "core/home.html")


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    return render(request, "core/dashboard.html")
