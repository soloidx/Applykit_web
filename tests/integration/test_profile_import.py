import os
import re
import zipfile
from pathlib import Path

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import Account
from apps.ai.conf import FEATURE_CANDIDATE_PROFILE
from apps.ai.errors import INTERNAL_ERROR, INVALID_RESPONSE
from apps.ai.models import AIOperationReservation, AIOperationSwitch
from apps.ai.schemas import CandidateProfileExtraction
from apps.ai.services import accept_consent
from apps.profiles.imports import PROFILE_FIELDS
from apps.profiles.models import CandidateProfile
from apps.profiles.versioning import profile_version_token
from tests.integration.ai_test_support import ai_overrides, enable_switches
from tests.unit.docx_support import build_docx
from tests.unit.pdf_support import build_pdf

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_CONTENT_TYPE = "application/pdf"
TOKEN_PATTERN = re.compile(r'name="version_token" value="([^"]+)"')


def verified_candidate(email: str, *, consent: bool = True) -> Account:
    account = Account.objects.create_user(email, "a-secure-password")
    EmailAddress.objects.create(user=account, email=account.email, primary=True, verified=True)
    if consent:
        accept_consent(account=account)
    return account


def source_bytes(tmp_path: Path, paragraphs: list[str] | None = None) -> bytes:
    path = build_docx(tmp_path / "resume.docx", paragraphs=paragraphs or ["Jane Doe", "Engineer"])
    return path.read_bytes()


def pdf_source_bytes(tmp_path: Path, pages: list[str] | None = None) -> bytes:
    path = build_pdf(
        tmp_path / "resume.pdf",
        pages=pages or ["Jane Doe\nPlatform Engineer", "Built reliable tools."],
    )
    return path.read_bytes()


def large_source_bytes(tmp_path: Path, size: int = 3 * 1024 * 1024) -> bytes:
    """A valid DOCX that exceeds Django's default in-memory upload threshold."""
    path = build_docx(tmp_path / "large.docx", paragraphs=["Jane Doe", "Engineer"])
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("word/media/large.bin", os.urandom(size))
    return path.read_bytes()


def profile_extraction(**overrides: object) -> CandidateProfileExtraction:
    values: dict[str, object] = {
        "full_name": "Jane Doe",
        "professional_title": "Platform engineer",
        "professional_summary": "Builds reliable developer tools.",
        "phone_number": None,
        "location": "London",
        "contact_email": None,
        "experiences": (),
        "educations": (),
        "projects": (),
        "skills": (),
        "languages": (),
        "warnings": (),
    }
    values.update(overrides)
    return CandidateProfileExtraction(**values)  # type: ignore[arg-type]


class FakeAI:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[tuple[Account, str]] = []

    def __call__(self, account: Account, text: str) -> CandidateProfileExtraction:
        self.calls.append((account, text))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, CandidateProfileExtraction)
        return result


@pytest.fixture(autouse=True)
def enabled_ai_settings():
    with override_settings(AI_IMPORTS=ai_overrides()):
        enable_switches()
        yield


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch):
    fake = FakeAI(profile_extraction())
    monkeypatch.setattr("apps.profiles.import_views.extract_candidate_profile", fake)
    return fake


def upload(
    client: Client,
    data: bytes,
    *,
    filename: str = "resume.docx",
    content_type: str = DOCX_CONTENT_TYPE,
):
    return client.post(
        reverse("profile_import_process"),
        {"source": SimpleUploadedFile(filename, data, content_type=content_type)},
    )


def review_token(response) -> str:
    match = TOKEN_PATTERN.search(response.content.decode())
    assert match is not None
    return match.group(1)


def record_handler_instantiations(record: list[object]):
    class SpyHandler:
        def __init__(self, request: object) -> None:
            record.append(request)

    return SpyHandler


def reviewed_values(**overrides: str) -> dict[str, str]:
    values = {
        "full_name": "Jane Doe",
        "timezone": "Europe/London",
        "contact_email": "jane@example.com",
        "professional_title": "Platform engineer",
        "professional_summary": "Builds reliable developer tools.",
        "phone_number": "",
        "location": "London",
        "linkedin_url": "",
        "portfolio_url": "",
        "confirm_full_name": "on",
    }
    values.update(overrides)
    return values


class TestOnboardingPaths:
    def test_onboarding_presents_import_and_manual_paths_as_coequal(self) -> None:
        account = verified_candidate("onboarding@example.com")
        client = Client()
        client.force_login(account)

        response = client.get(reverse("profile"))
        content = response.content.decode()

        assert response.status_code == 200
        assert f'action="{reverse("profile_import_process")}"' in content
        assert f'action="{reverse("profile")}"' in content
        assert 'enctype="multipart/form-data"' in content
        assert "application/pdf" in content
        assert ".docx" in content

    def test_import_requires_verified_authentication(self) -> None:
        response = Client().post(reverse("profile_import_process"), {})

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("account_login"))

    def test_process_route_rejects_non_post_requests(self) -> None:
        account = verified_candidate("get-import@example.com")
        client = Client()
        client.force_login(account)

        assert client.get(reverse("profile_import_process")).status_code == 405


class TestSourceGating:
    def test_missing_consent_redirects_to_disclosure_before_reading_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        account = verified_candidate("no-consent@example.com", consent=False)
        client = Client()
        client.force_login(account)
        instantiated: list[object] = []
        monkeypatch.setattr(
            "apps.profiles.import_views.ProfileSourceUploadHandler",
            record_handler_instantiations(instantiated),
        )

        response = upload(client, source_bytes(tmp_path))

        assert response.status_code == 302
        assert response.headers["Location"].startswith(reverse("ai_consent"))
        assert instantiated == []

    def test_disabled_ai_fails_closed_before_reading_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        account = verified_candidate("disabled@example.com")
        client = Client()
        client.force_login(account)
        AIOperationSwitch.objects.all().delete()
        instantiated: list[object] = []
        monkeypatch.setattr(
            "apps.profiles.import_views.ProfileSourceUploadHandler",
            record_handler_instantiations(instantiated),
        )

        response = upload(client, source_bytes(tmp_path))

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "not available" in response.content.decode().lower()
        assert instantiated == []
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_header_based_csrf_is_required_before_multipart_parsing(self, tmp_path: Path) -> None:
        account = verified_candidate("csrf@example.com")
        client = Client(enforce_csrf_checks=True)
        client.force_login(account)
        client.get(reverse("profile"))
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        data = source_bytes(tmp_path)

        rejected = upload(client, data)
        accepted = client.post(
            reverse("profile_import_process"),
            {"source": SimpleUploadedFile("resume.docx", data, content_type=DOCX_CONTENT_TYPE)},
            headers={"X-CSRFToken": token},
        )

        assert rejected.status_code == 403
        assert accepted.status_code == 200
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_in_flight_operation_blocks_before_reading_the_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        account = verified_candidate("inflight@example.com")
        client = Client()
        client.force_login(account)
        AIOperationReservation.objects.create(
            account=account,
            feature=FEATURE_CANDIDATE_PROFILE,
        )
        instantiated: list[object] = []
        monkeypatch.setattr(
            "apps.profiles.import_views.ProfileSourceUploadHandler",
            record_handler_instantiations(instantiated),
        )

        response = upload(client, source_bytes(tmp_path))

        assert response.status_code == 200
        assert "limit" in response.content.decode().lower()
        assert instantiated == []
        assert not CandidateProfile.objects.filter(account=account).exists()


class TestReviewDraft:
    def test_upload_renders_no_store_review_without_persisting(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("review@example.com")
        client = Client()
        client.force_login(account)

        response = upload(client, source_bytes(tmp_path, ["Jane Doe is a platform engineer."]))

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        content = response.content.decode()
        assert "Check every source fact" in content
        assert 'name="full_name"' in content
        assert 'name="timezone"' in content
        assert 'name="confirm_full_name"' in content
        assert "browser" in content.lower()
        assert not CandidateProfile.objects.filter(account=account).exists()
        assert fake_ai.calls and fake_ai.calls[0][0] == account

    def test_supported_documents_above_djangos_default_memory_size_reach_extraction(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("large@example.com")
        client = Client()
        client.force_login(account)

        response = upload(client, large_source_bytes(tmp_path))

        assert response.status_code == 200
        assert "Check every source fact" in response.content.decode()
        assert fake_ai.calls

    def test_explicit_source_email_wins_and_account_email_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        account = verified_candidate("emails@example.com")
        client = Client()
        client.force_login(account)

        default_fake = FakeAI(profile_extraction(contact_email=None))
        monkeypatch.setattr("apps.profiles.import_views.extract_candidate_profile", default_fake)
        default_review = upload(client, source_bytes(tmp_path))
        default_content = default_review.content.decode()

        source_fake = FakeAI(profile_extraction(contact_email="source@example.com"))
        monkeypatch.setattr("apps.profiles.import_views.extract_candidate_profile", source_fake)
        source_review = upload(client, source_bytes(tmp_path))
        source_content = source_review.content.decode()

        assert f'value="{account.email}"' in default_content
        assert 'value="source@example.com"' in source_content

    def test_unusable_core_result_fails_safely_without_a_draft(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        account = verified_candidate("unusable@example.com")
        client = Client()
        client.force_login(account)
        fake = FakeAI(
            profile_extraction(
                full_name=None,
                professional_title=None,
                professional_summary=None,
                phone_number=None,
                location=None,
                contact_email=None,
            )
        )
        monkeypatch.setattr("apps.profiles.import_views.extract_candidate_profile", fake)

        response = upload(client, source_bytes(tmp_path))

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "nothing was saved" in response.content.decode().lower()
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_extraction_failure_renders_safe_failure_without_persisting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        account = verified_candidate("failure@example.com")
        client = Client()
        client.force_login(account)
        from apps.ai.errors import AIError

        fake = FakeAI(AIError(INVALID_RESPONSE))
        monkeypatch.setattr("apps.profiles.import_views.extract_candidate_profile", fake)

        response = upload(client, source_bytes(tmp_path))

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "safely" in response.content.decode().lower()
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_malformed_document_fails_safely_and_ai_is_never_called(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("malformed@example.com")
        client = Client()
        client.force_login(account)

        response = upload(client, b"definitely not a docx")

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "could not be read" in response.content.decode().lower()
        assert fake_ai.calls == []
        assert not CandidateProfile.objects.filter(account=account).exists()

    @pytest.mark.parametrize(
        ("builder", "filename", "content_type", "expected_text"),
        [
            (source_bytes, "resume.docx", DOCX_CONTENT_TYPE, "Engineer"),
            (pdf_source_bytes, "resume.pdf", PDF_CONTENT_TYPE, "Platform Engineer"),
        ],
        ids=["docx", "pdf"],
    )
    def test_accepted_docx_and_pdf_share_the_review_journey(
        self,
        fake_ai: FakeAI,
        tmp_path: Path,
        builder,
        filename: str,
        content_type: str,
        expected_text: str,
    ) -> None:
        account = verified_candidate("shared-review@example.com")
        client = Client()
        client.force_login(account)

        response = upload(client, builder(tmp_path), filename=filename, content_type=content_type)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "Check every source fact" in response.content.decode()
        assert not CandidateProfile.objects.filter(account=account).exists()
        assert fake_ai.calls and expected_text in fake_ai.calls[0][1]

    def test_encrypted_pdf_is_unsupported_and_ai_is_never_called(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("encrypted-pdf@example.com")
        client = Client()
        client.force_login(account)
        data = build_pdf(tmp_path / "encrypted.pdf", pages=["Secret"], encrypt="pw").read_bytes()

        response = upload(client, data, filename="resume.pdf", content_type=PDF_CONTENT_TYPE)

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        content = response.content.decode().lower()
        assert "not a supported" in content
        assert "your saved data is unchanged" in content
        assert fake_ai.calls == []
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_image_only_pdf_is_unsupported_and_ai_is_never_called(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("image-pdf@example.com")
        client = Client()
        client.force_login(account)
        data = build_pdf(tmp_path / "image-only.pdf", image_only=True).read_bytes()

        response = upload(client, data, filename="scan.pdf", content_type=PDF_CONTENT_TYPE)

        assert response.status_code == 200
        assert "not a supported" in response.content.decode().lower()
        assert fake_ai.calls == []
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_pdf_over_page_budget_is_reported_safely(self, fake_ai: FakeAI, tmp_path: Path) -> None:
        account = verified_candidate("pages-pdf@example.com")
        client = Client()
        client.force_login(account)
        data = build_pdf(
            tmp_path / "many.pdf", pages=[f"Page {index}" for index in range(51)]
        ).read_bytes()

        response = upload(client, data, filename="many.pdf", content_type=PDF_CONTENT_TYPE)

        assert response.status_code == 200
        content = response.content.decode().lower()
        assert "too large or too long" in content
        assert "your saved data is unchanged" in content
        assert fake_ai.calls == []
        assert not CandidateProfile.objects.filter(account=account).exists()


class TestAtomicSave:
    def test_save_creates_the_initial_profile_atomically(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("save@example.com")
        client = Client()
        client.force_login(account)
        review = upload(client, source_bytes(tmp_path))
        token = review_token(review)

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token=token),
        )

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("dashboard")
        profile = CandidateProfile.objects.get(account=account)
        assert profile.full_name == "Jane Doe"
        assert profile.timezone == "Europe/London"

    def test_pdf_source_saves_identically_after_review(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("pdf-save@example.com")
        client = Client()
        client.force_login(account)
        review = upload(
            client,
            pdf_source_bytes(tmp_path),
            filename="resume.pdf",
            content_type=PDF_CONTENT_TYPE,
        )
        token = review_token(review)

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token=token),
        )

        assert response.status_code == 302
        assert response.headers["Location"] == reverse("dashboard")
        profile = CandidateProfile.objects.get(account=account)
        assert profile.full_name == "Jane Doe"
        assert profile.timezone == "Europe/London"

    def test_review_requires_confirmation_and_timezone(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("required@example.com")
        client = Client()
        client.force_login(account)
        token = review_token(upload(client, source_bytes(tmp_path)))

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token=token, timezone="Mars/Colony", confirm_full_name=""),
        )

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        content = response.content.decode()
        assert "valid IANA timezone" in content
        assert "Confirm your full name" in content
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_concurrent_profile_is_a_stale_save_without_replacement(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("stale@example.com")
        client = Client()
        client.force_login(account)
        token = profile_version_token(account, None)
        original = CandidateProfile.objects.create(
            account=account,
            full_name="Original Candidate",
            timezone="Europe/London",
            contact_email="original@example.com",
        )

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token=token),
        )

        assert response.status_code == 200
        content = response.content.decode()
        assert "changed since this import" in content
        original.refresh_from_db()
        assert original.full_name == "Original Candidate"
        assert CandidateProfile.objects.filter(account=account).count() == 1

    def test_a_token_for_another_account_is_rejected(self, fake_ai: FakeAI, tmp_path: Path) -> None:
        owner = verified_candidate("token-owner@example.com")
        other = verified_candidate("token-other@example.com", consent=False)
        client = Client()
        client.force_login(other)
        forged = profile_version_token(owner, None)

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token=forged),
        )

        assert response.status_code == 200
        assert "changed since this import" in response.content.decode()
        assert not CandidateProfile.objects.filter(account=other).exists()

    def test_a_tampered_token_is_rejected(self, fake_ai: FakeAI) -> None:
        account = verified_candidate("tampered@example.com")
        client = Client()
        client.force_login(account)

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token="not-a-valid-token"),
        )

        assert response.status_code == 200
        assert "changed since this import" in response.content.decode()
        assert not CandidateProfile.objects.filter(account=account).exists()

    def test_save_is_ownership_scoped_and_proposes_only_core_fields(
        self, fake_ai: FakeAI, tmp_path: Path
    ) -> None:
        account = verified_candidate("scoped@example.com")
        other = verified_candidate("scoped-other@example.com", consent=False)
        client = Client()
        client.force_login(account)
        token = review_token(upload(client, source_bytes(tmp_path)))

        response = client.post(
            reverse("profile_import_save"),
            reviewed_values(version_token=token),
        )

        assert response.status_code == 302
        assert CandidateProfile.objects.filter(account=account).exists()
        assert not CandidateProfile.objects.filter(account=other).exists()
        profile = CandidateProfile.objects.get(account=account)
        assert set(PROFILE_FIELDS).issubset(
            {field.name for field in CandidateProfile._meta.get_fields()}
        )
        assert profile.experiences.count() == 0
        assert profile.profile_skills.count() == 0


def test_internal_ai_failure_category_is_rendered_safely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account = verified_candidate("internal@example.com")
    client = Client()
    client.force_login(account)
    from apps.ai.errors import AIError

    fake = FakeAI(AIError(INTERNAL_ERROR))
    monkeypatch.setattr("apps.profiles.import_views.extract_candidate_profile", fake)

    response = upload(client, source_bytes(tmp_path))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "went wrong" in response.content.decode().lower()
