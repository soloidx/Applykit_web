from django.test import override_settings

from apps.documents.conf import current_settings


def test_pdf_page_budget_defaults_to_50():
    with override_settings(DOCUMENT_EXTRACTION={}):
        assert current_settings().max_pdf_pages == 50


def test_pdf_page_budget_is_operator_configurable():
    with override_settings(DOCUMENT_EXTRACTION={"MAX_PDF_PAGES": 7}):
        assert current_settings().max_pdf_pages == 7
