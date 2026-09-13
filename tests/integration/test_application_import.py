import pytest
from allauth.account.models import EmailAddress
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.ai.conf import FEATURE_JOB_POSTING
from apps.ai.errors import INTERNAL_ERROR, INVALID_RESPONSE, AIError
from apps.ai.models import AIOperationReservation, AIOperationSwitch
from apps.ai.schemas import JobPostingExtraction
from apps.ai.services import accept_consent
from apps.applications.imports import InvalidApplicationContents, create_imported_application
from apps.applications.models import Company, JobApplication
from apps.campaigns.models import Campaign
from tests.integration.ai_test_support import ai_overrides, enable_switches

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def verified_candidate(email: str, *, consent: bool = True) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    Campaign.objects.create(
        account=account,
        weekly_target=5,
        monthly_target=20,
        timezone="Europe/London",
    )
    if consent:
        accept_consent(account=account)
    return account


def posting_extraction(**overrides: object) -> JobPostingExtraction:
    values: dict[str, object] = {
        "company_name": "Example Careers",
        "company_website": None,
        "role_title": "Platform engineer",
        "job_description": "Build dependable internal systems.",
        "location": "Remote",
        "compensation": "100000 GBP",
        "posting_url": None,
        "requirements": (),
        "warnings": (),
    }
    values.update(overrides)
    return JobPostingExtraction(**values)  # type: ignore[arg-type]


class FakeAI:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[tuple[Account, str]] = []

    def __call__(self, account: Account, text: str) -> JobPostingExtraction:
        self.calls.append((account, text))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, JobPostingExtraction)
        return result


@pytest.fixture(autouse=True)
def enabled_ai_settings():
    with override_settings(AI_IMPORTS=ai_overrides()):
        enable_switches()
        yield


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch):
    fake = FakeAI(posting_extraction())
    monkeypatch.setattr("apps.applications.import_views.extract_job_posting", fake)
    return fake


def process(client: Client, posting_text: str = "Platform engineer. Build it."):
    return client.post(reverse("application_import_process"), {"posting_text": posting_text})


def reviewed_values(**overrides: str) -> dict[str, str]:
    values = {
        "company_name": "Example Careers",
        "company_website": "",
        "role_title": "Platform engineer",
        "job_description": "Build dependable internal systems.",
        "location": "Remote",
        "compensation": "100000 GBP",
        "posting_url": "",
        "creation_token": "test-creation-token",
    }
    values.update(overrides)
    return values


def make_application(account: Account, **overrides: object) -> JobApplication:
    company = Company.objects.create(name="Existing Co")
    campaign = Campaign.objects.get(account=account)
    values: dict[str, object] = {
        "account": account,
        "campaign": campaign,
        "company": company,
        "role_title": "Existing role",
        "job_description": "Existing description.",
        "posting_url": "",
    }
    values.update(overrides)
    return JobApplication.objects.create(**values)  # type: ignore[arg-type]


class TestSourceStep:
    def test_source_offers_paste_and_manual_paths_as_coequal(self) -> None:
        account = verified_candidate("source@example.com")
        client = Client()
        client.force_login(account)

        response = client.get(reverse("application_create"))
        content = response.content.decode()

        assert response.status_code == 200
        assert f'action="{reverse("application_import_process")}"' in content
        assert f'action="{reverse("application_create")}"' in content
        assert 'name="posting_text"' in content
        assert "never retrieves the posting" in content.lower()
        assert "01</span>Source" in content

    def test_import_requires_verified_authentication(self) -> None:
        response = Client().post(reverse("application_import_process"), {})

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("account_login"))

    def test_process_route_rejects_non_post_requests(self) -> None:
        account = verified_candidate("get-import@example.com")
        client = Client()
        client.force_login(account)

        assert client.get(reverse("application_import_process")).status_code == 405


class TestProcessGating:
    def test_missing_consent_redirects_to_disclosure_before_calling_ai(
        self, fake_ai: FakeAI
    ) -> None:
        account = verified_candidate("no-consent@example.com", consent=False)
        client = Client()
        client.force_login(account)

        response = process(client)

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("ai_consent"))
        assert fake_ai.calls == []
        assert not JobApplication.objects.filter(account=account).exists()

    def test_disabled_ai_fails_closed_without_calling_ai(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("disabled@example.com")
        client = Client()
        client.force_login(account)
        AIOperationSwitch.objects.all().delete()

        response = process(client)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "not available" in response.content.decode().lower()
        assert fake_ai.calls == []
        assert not JobApplication.objects.filter(account=account).exists()

    def test_in_flight_operation_blocks_before_calling_ai(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("inflight@example.com")
        client = Client()
        client.force_login(account)
        AIOperationReservation.objects.create(account=account, feature=FEATURE_JOB_POSTING)

        response = process(client)

        assert response.status_code == 200
        assert "limit" in response.content.decode().lower()
        assert fake_ai.calls == []

    def test_processing_requires_an_active_campaign(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("no-campaign@example.com")
        Campaign.objects.filter(account=account).update(status=Campaign.Status.ARCHIVED)
        client = Client()
        client.force_login(account)

        response = process(client)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "activate a campaign" in response.content.decode().lower()
        assert fake_ai.calls == []
        assert not JobApplication.objects.filter(account=account).exists()


class TestPastedText:
    def test_empty_and_over_limit_text_is_rejected_without_ai(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("limits@example.com")
        client = Client()
        client.force_login(account)

        empty = process(client, "   ")
        too_long = process(client, "a" * 50_001)

        assert empty.status_code == 200
        assert "paste the job posting" in empty.content.decode().lower()
        assert too_long.status_code == 200
        assert fake_ai.calls == []
        assert not JobApplication.objects.filter(account=account).exists()

    def test_text_is_normalized_before_it_reaches_ai(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("normalize@example.com")
        client = Client()
        client.force_login(account)

        process(client, "Role\x00: Engineer\r\n\r\nBuild\tservices.")

        assert fake_ai.calls
        assert fake_ai.calls[0][0] == account
        assert fake_ai.calls[0][1] == "Role: Engineer\n\nBuild services."


class TestReviewDraft:
    def test_usable_result_renders_no_store_review_without_persisting(
        self, fake_ai: FakeAI
    ) -> None:
        account = verified_candidate("review@example.com")
        client = Client()
        client.force_login(account)

        response = process(client)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        content = response.content.decode()
        assert "Check every stated fact" in content
        assert 'name="company_name"' in content
        assert 'name="company_website"' in content
        assert 'name="role_title"' in content
        assert 'name="job_description"' in content
        assert 'name="posting_url"' in content
        assert 'name="creation_token"' in content
        assert "browser" in content.lower()
        assert 'name="source"' not in content
        assert 'name="private_notes"' not in content
        assert not JobApplication.objects.filter(account=account).exists()

    def test_role_title_alone_is_usable_with_a_description_warning(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("partial@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(job_description=None)]

        response = process(client)

        content = response.content.decode()
        assert response.status_code == 200
        assert "Platform engineer" in content
        assert "Not stated in the posting" in content

    def test_description_alone_is_usable_with_a_title_warning(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("description-only@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(role_title=None)]

        response = process(client)

        content = response.content.decode()
        assert response.status_code == 200
        assert "Build dependable internal systems." in content
        assert "Not stated in the posting" in content

    def test_unusable_result_fails_safely_without_a_draft(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("unusable@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(role_title=None, job_description=None)]

        response = process(client)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "nothing was saved" in response.content.decode().lower()
        assert not JobApplication.objects.filter(account=account).exists()

    def test_extraction_failure_renders_safe_failure_without_persisting(
        self, fake_ai: FakeAI
    ) -> None:
        account = verified_candidate("failure@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [AIError(INVALID_RESPONSE)]

        response = process(client)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "safely" in response.content.decode().lower()
        assert not JobApplication.objects.filter(account=account).exists()

    def test_internal_ai_failure_is_rendered_safely(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("internal@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [AIError(INTERNAL_ERROR)]

        response = process(client)

        assert response.status_code == 200
        assert "went wrong" in response.content.decode().lower()

    def test_failure_page_offers_the_manual_fallback(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("fallback@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [AIError(INVALID_RESPONSE)]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "manually" in content.lower()
        assert f'href="{reverse("application_create")}"' in content


class TestCompanyIdentityReview:
    def test_stated_website_matching_an_existing_company_is_reused(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("reuse@example.com")
        existing = Company.objects.create(name="Example", canonical_domain="example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(company_website="https://www.example.com/careers")]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "shared Company" in content
        assert existing.name in content
        assert "no new Company identity is created" in content

    def test_domain_alias_matching_an_existing_company_is_reused(self, fake_ai: FakeAI) -> None:
        from apps.applications.models import CompanyDomainAlias

        account = verified_candidate("alias@example.com")
        existing = Company.objects.create(name="Example", canonical_domain="example.com")
        CompanyDomainAlias.objects.create(company=existing, domain="example.co.uk")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(company_website="jobs.example.co.uk")]

        response = process(client)

        assert response.status_code == 200
        assert existing.name in response.content.decode()

    def test_name_only_provisional_company_shows_the_shared_identity_disclosure(
        self, fake_ai: FakeAI
    ) -> None:
        account = verified_candidate("provisional@example.com")
        client = Client()
        client.force_login(account)

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "shared public Company" in content
        assert "not private to your account" in content

    def test_extraction_never_derives_a_website_from_the_posting_host(
        self, fake_ai: FakeAI
    ) -> None:
        account = verified_candidate("no-derive@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [
            posting_extraction(
                company_name="Example",
                company_website=None,
                posting_url="https://jobs.example.com/roles/1",
            )
        ]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert 'name="company_website"' in content
        assert "shared public Company" in content


class TestProvenanceReview:
    def test_extracted_url_is_sanitized_before_review(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("sanitize@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [
            posting_extraction(posting_url="https://example.com/jobs/1?utm_source=x&id=7#apply")
        ]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "https://example.com/jobs/1?id=7" in content
        assert "utm_source" not in content
        assert "#apply" not in content

    def test_malformed_extracted_url_is_removed_with_a_warning(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("malformed-extracted@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(posting_url="https://[::1")]

        response = process(client)

        assert response.status_code == 200
        assert "looked unsafe" in response.content.decode()

    def test_unsafe_extracted_url_is_removed_with_a_warning(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("unsafe-extracted@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [
            posting_extraction(posting_url="https://example.com/jobs/1?access_token=secret")
        ]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "looked unsafe" in content
        assert "access_token" not in content

    def test_duplicate_warning_lists_company_role_campaign_and_stage(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("duplicate@example.com")
        existing = make_application(
            account,
            posting_url="https://example.com/jobs/1",
            role_title="Existing role",
        )
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(posting_url="https://example.com/jobs/1")]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "matches an application you already have" in content
        assert existing.company.name in content
        assert "Existing role" in content
        assert f"Campaign #{existing.campaign_id}" in content
        assert existing.get_stage_display() in content

    def test_duplicate_warning_is_account_private(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("owner@example.com")
        other = verified_candidate("other@example.com", consent=False)
        make_application(other, posting_url="https://example.com/jobs/1")
        client = Client()
        client.force_login(account)
        fake_ai.results = [posting_extraction(posting_url="https://example.com/jobs/1")]

        response = process(client)
        content = response.content.decode()

        assert response.status_code == 200
        assert "matches an application you already have" not in content


class TestAtomicSave:
    def test_save_creates_one_draft_application_and_its_company_atomically(
        self, fake_ai: FakeAI
    ) -> None:
        account = verified_candidate("save@example.com")
        campaign = Campaign.objects.get(account=account)
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(),
        )

        assert response.status_code == 302
        application = JobApplication.objects.get()
        assert response.headers["Location"] == reverse("application_detail", args=[application.pk])
        assert application.account == account
        assert application.campaign == campaign
        assert application.company.name == "Example Careers"
        assert application.company.canonical_domain is None
        assert application.stage == JobApplication.Stage.DRAFT
        assert application.role_title == "Platform engineer"
        assert application.location == "Remote"
        assert application.source == ""
        assert application.private_notes == ""

    def test_save_reuses_an_existing_company_on_a_domain_match(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("reuse-save@example.com")
        existing = Company.objects.create(name="Example", canonical_domain="example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(company_website="https://www.example.com/careers"),
        )

        assert response.status_code == 302
        application = JobApplication.objects.get()
        assert application.company == existing
        assert Company.objects.count() == 1

    def test_save_persists_the_reviewed_sanitized_url(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("provenance@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(posting_url="https://example.com/jobs/1?utm_source=x&id=7#apply"),
        )

        assert response.status_code == 302
        application = JobApplication.objects.get()
        assert application.posting_url == "https://example.com/jobs/1?id=7"

    def test_save_rejects_an_unsafe_url_and_creates_nothing(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("unsafe-save@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(posting_url="https://user:secret@example.com/jobs/1"),
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "safe HTTP(S) posting URL" in response.content.decode()
        assert not JobApplication.objects.filter(account=account).exists()
        assert not Company.objects.filter(name="Example Careers").exists()

    def test_save_rejects_a_malformed_url_and_creates_nothing(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("malformed-save@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(posting_url="https://[::1"),
        )

        assert response.status_code == 200
        assert "safe HTTP(S) posting URL" in response.content.decode()
        assert not JobApplication.objects.filter(account=account).exists()

    def test_save_creates_a_company_with_a_stated_unmatched_website(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("unmatched-website@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(
                company_name="Brand New Co",
                company_website="https://careers.brandnewco.com/roles",
            ),
        )

        assert response.status_code == 302
        company = JobApplication.objects.get().company
        assert company.name == "Brand New Co"
        assert company.canonical_domain == "brandnewco.com"

    def test_save_requires_company_role_title_and_description(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("required@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(company_name="", role_title="", job_description=""),
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert b"This field is required." in response.content
        assert not JobApplication.objects.filter(account=account).exists()

    def test_save_without_an_active_campaign_creates_nothing(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("archive@example.com")
        Campaign.objects.filter(account=account).update(status=Campaign.Status.ARCHIVED)
        client = Client()
        client.force_login(account)

        response = client.post(reverse("application_import_save"), reviewed_values())

        assert response.status_code == 200
        assert "activate a campaign" in response.content.decode().lower()
        assert not JobApplication.objects.filter(account=account).exists()
        assert not Company.objects.filter(name="Example Careers").exists()

    def test_persistence_failure_creates_nothing_and_redisplays(
        self, fake_ai: FakeAI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account = verified_candidate("persist@example.com")
        client = Client()
        client.force_login(account)

        def fail_save(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Persistence failed")

        monkeypatch.setattr(JobApplication, "save", fail_save)

        response = client.post(reverse("application_import_save"), reviewed_values())

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "nothing was created" in response.content.decode().lower()
        assert b"Platform engineer" in response.content
        assert not JobApplication.objects.filter(account=account).exists()
        assert not Company.objects.filter(name="Example Careers").exists()

    def test_save_is_scoped_to_the_authenticated_account(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("scoped@example.com")
        other = verified_candidate("other-scoped@example.com", consent=False)
        client = Client()
        client.force_login(account)

        client.post(reverse("application_import_save"), reviewed_values())

        assert JobApplication.objects.filter(account=account).count() == 1
        assert not JobApplication.objects.filter(account=other).exists()


class TestCreationTokenIdempotency:
    def test_repeated_submission_returns_the_original_result(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("idempotent@example.com")
        client = Client()
        client.force_login(account)
        values = reviewed_values(creation_token="one-review-token")

        first = client.post(reverse("application_import_save"), values)
        second = client.post(reverse("application_import_save"), values)

        assert first.headers["Location"] == second.headers["Location"]
        assert JobApplication.objects.filter(account=account).count() == 1

    def test_a_new_token_creates_a_deliberate_duplicate(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("duplicate-save@example.com")
        client = Client()
        client.force_login(account)

        client.post(
            reverse("application_import_save"),
            reviewed_values(creation_token="first-token", posting_url="https://example.com/jobs/1"),
        )
        client.post(
            reverse("application_import_save"),
            reviewed_values(
                creation_token="second-token",
                posting_url="https://example.com/jobs/1",
                confirm_duplicate="on",
            ),
        )

        assert JobApplication.objects.filter(account=account).count() == 2

    def test_duplicate_requires_explicit_confirmation(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("explicit@example.com")
        make_application(account, posting_url="https://example.com/jobs/1")
        client = Client()
        client.force_login(account)

        blocked = client.post(
            reverse("application_import_save"),
            reviewed_values(posting_url="https://example.com/jobs/1"),
        )
        assert blocked.status_code == 200
        assert "matches an application you already have" in blocked.content.decode()
        assert JobApplication.objects.filter(account=account).count() == 1

        allowed = client.post(
            reverse("application_import_save"),
            reviewed_values(
                posting_url="https://example.com/jobs/1",
                confirm_duplicate="on",
            ),
        )
        assert allowed.status_code == 302
        assert JobApplication.objects.filter(account=account).count() == 2


class TestHtmxParity:
    def test_htmx_consent_required_redirects_via_header(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("htmx-consent@example.com", consent=False)
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_process"),
            {"posting_text": "Platform engineer. Build it."},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["HX-Redirect"].startswith(reverse("ai_consent"))
        assert fake_ai.calls == []

    def test_htmx_process_returns_the_no_store_review_stage(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("htmx-process@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_process"),
            {"posting_text": "Platform engineer. Build it."},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert 'id="import-stage"' in response.content.decode()

    def test_htmx_save_redirects_via_header(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("htmx-save@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("application_import_save"),
            reviewed_values(),
            headers={"HX-Request": "true"},
        )

        application = JobApplication.objects.get()
        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == reverse(
            "application_detail", args=[application.pk]
        )

    def test_htmx_failure_uses_the_same_safe_no_store_response(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("htmx-fail@example.com")
        client = Client()
        client.force_login(account)
        fake_ai.results = [AIError(INVALID_RESPONSE)]

        response = client.post(
            reverse("application_import_process"),
            {"posting_text": "Platform engineer. Build it."},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert 'id="import-stage"' in response.content.decode()


def test_invalid_review_values_are_revalidated_and_do_not_raise() -> None:
    account = verified_candidate("revalidate@example.com")
    client = Client()
    client.force_login(account)

    response = client.post(
        reverse("application_import_save"),
        {"company_name": "", "role_title": "x", "job_description": "y"},
    )

    assert response.status_code == 200
    assert not JobApplication.objects.filter(account=account).exists()


def test_create_imported_application_rejects_invalid_values() -> None:
    account = verified_candidate("service-revalidate@example.com")

    with pytest.raises(InvalidApplicationContents):
        create_imported_application(
            account=account,
            values={
                "company_name": "",
                "company_website": "",
                "role_title": "",
                "job_description": "",
            },
            token="service-token",
        )
