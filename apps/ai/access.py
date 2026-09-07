from collections.abc import Callable
from functools import wraps
from typing import cast
from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.access import verified_account_required
from apps.accounts.models import Account
from apps.ai.services import has_current_consent


def ai_consent_required[View: Callable[..., HttpResponse]](view: View) -> View:
    """Require verified authentication and current AI consent for one AI entry point."""

    @verified_account_required
    @wraps(view)
    def gated(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        account = cast(Account, request.user)
        if not has_current_consent(account):
            consent_url = f"{reverse('ai_consent')}?next={quote(request.get_full_path())}"
            return redirect(consent_url)
        return view(request, *args, **kwargs)

    return cast(View, login_required(cast(Callable[..., HttpResponse], gated)))
