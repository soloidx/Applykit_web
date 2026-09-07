"""Extraction schemas and local result validation.

The JSON Schema constants travel to the provider under strict structured
output; the parse functions enforce the same contract locally, so a response
is trusted only after local validation. Source grounding is verified locally:
each Skill proposal's exact wording must appear in the canonical source text.
Uncertainty is expressed through nulls and typed warnings, never invented
defaults or confidence scores.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

__all__ = [
    "PROFILE_SCHEMA",
    "PROFILE_SCHEMA_NAME",
    "POSTING_SCHEMA",
    "POSTING_SCHEMA_NAME",
    "ResultRejected",
    "parse_profile_result",
    "parse_posting_result",
]

PROFILE_SCHEMA_NAME = "candidate_profile_extraction_v1"
POSTING_SCHEMA_NAME = "job_posting_extraction_v1"

_SUITABILITIES = ("suitable", "unsuitable")
_STATUSES = ("resolved", "unknown", "ambiguous")
_PROFICIENCIES = ("beginner", "intermediate", "advanced", "fluent", "native")

_TEXT_CAP = 200
_SUMMARY_CAP = 5_000
_SECTION_TEXT_CAP = 5_000
_HIGHLIGHT_CAP = 500
_JOB_DESCRIPTION_CAP = 20_000
_LINE_CAP = 255
_EMAIL_CAP = 254
_URL_CAP = 2_048

_MAX_RECORDS = 30
_MAX_SKILLS_PER_LOCATION = 50
_MAX_HIGHLIGHTS = 20
_MAX_LANGUAGES = 30
_MAX_REQUIREMENTS = 60

_DATE_PATTERN = re.compile(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_PATTERN = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

# Local grounding predicate built once per operation from the source text.
Grounding = Callable[[str], bool]

_TEXT_NULL = {"type": ["string", "null"], "maxLength": _TEXT_CAP}
_LINE_NULL = {"type": ["string", "null"], "maxLength": _LINE_CAP}
_DATE_NULL = {"type": ["string", "null"], "pattern": "^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$"}


class ResultRejected(Exception):
    """The decoded output failed local validation; it is not trusted."""


@dataclass(frozen=True)
class ExtractedDate:
    """A source date at exactly the precision the source stated."""

    text: str
    year: int
    month: int | None
    day: int | None

    @property
    def is_complete(self) -> bool:
        return self.month is not None and self.day is not None


@dataclass(frozen=True)
class SkillProposal:
    """One proposed skill; its nested position is the local evidence."""

    source_wording: str
    proposed_concept: str | None
    suitability: str
    status: str


@dataclass(frozen=True)
class ExperienceExtraction:
    role: str | None
    organization: str | None
    location: str | None
    start_date: ExtractedDate | None
    end_date: ExtractedDate | None
    is_current: bool
    description: str | None
    highlights: tuple[str, ...]
    skills: tuple[SkillProposal, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class EducationExtraction:
    institution: str | None
    degree: str | None
    start_date: ExtractedDate | None
    end_date: ExtractedDate | None
    is_current: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ProjectExtraction:
    name: str | None
    description: str | None
    skills: tuple[SkillProposal, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class LanguageExtraction:
    name: str
    proficiency: str | None


@dataclass(frozen=True)
class CandidateProfileExtraction:
    full_name: str | None
    professional_title: str | None
    professional_summary: str | None
    phone_number: str | None
    location: str | None
    contact_email: str | None
    experiences: tuple[ExperienceExtraction, ...]
    educations: tuple[EducationExtraction, ...]
    projects: tuple[ProjectExtraction, ...]
    skills: tuple[SkillProposal, ...]
    languages: tuple[LanguageExtraction, ...]
    warnings: tuple[str, ...]

    def has_meaningful_facts(self) -> bool:
        return any(
            (
                self.full_name,
                self.professional_title,
                self.professional_summary,
                self.phone_number,
                self.location,
                self.contact_email,
                self.experiences,
                self.educations,
                self.projects,
                self.skills,
                self.languages,
            )
        )


@dataclass(frozen=True)
class RequirementProposal:
    """One proposed hard-skill requirement from a pasted posting."""

    source_wording: str
    proposed_concept: str | None
    suitability: str
    status: str
    classification: str | None


@dataclass(frozen=True)
class JobPostingExtraction:
    company_name: str | None
    company_website: str | None
    role_title: str | None
    job_description: str | None
    location: str | None
    compensation: str | None
    posting_url: str | None
    requirements: tuple[RequirementProposal, ...]
    warnings: tuple[str, ...]

    def has_meaningful_facts(self) -> bool:
        return any(
            (
                self.company_name,
                self.role_title,
                self.job_description,
                self.location,
                self.compensation,
                self.posting_url,
                self.requirements,
            )
        )


_SKILL_PROPERTIES: dict[str, Any] = {
    "source_wording": {"type": "string", "minLength": 1, "maxLength": _TEXT_CAP},
    "proposed_concept": _TEXT_NULL,
    "suitability": {"type": "string", "enum": list(_SUITABILITIES)},
    "status": {"type": "string", "enum": list(_STATUSES)},
}
_SKILL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_SKILL_PROPERTIES),
    "properties": _SKILL_PROPERTIES,
}
_EXPERIENCE_PROPERTIES: dict[str, Any] = {
    "role": _TEXT_NULL,
    "organization": _TEXT_NULL,
    "location": _TEXT_NULL,
    "start_date": _DATE_NULL,
    "end_date": _DATE_NULL,
    "is_current": {"type": "boolean"},
    "description": {"type": ["string", "null"], "maxLength": _SECTION_TEXT_CAP},
    "highlights": {
        "type": "array",
        "items": {"type": "string", "maxLength": _HIGHLIGHT_CAP},
        "maxItems": _MAX_HIGHLIGHTS,
    },
    "skills": {"type": "array", "items": _SKILL_SCHEMA, "maxItems": _MAX_SKILLS_PER_LOCATION},
}
_EXPERIENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_EXPERIENCE_PROPERTIES),
    "properties": _EXPERIENCE_PROPERTIES,
}
_EDUCATION_PROPERTIES: dict[str, Any] = {
    "institution": _TEXT_NULL,
    "degree": _TEXT_NULL,
    "start_date": _DATE_NULL,
    "end_date": _DATE_NULL,
    "is_current": {"type": "boolean"},
}
_EDUCATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_EDUCATION_PROPERTIES),
    "properties": _EDUCATION_PROPERTIES,
}
_PROJECT_PROPERTIES: dict[str, Any] = {
    "name": _TEXT_NULL,
    "description": {"type": ["string", "null"], "maxLength": _SECTION_TEXT_CAP},
    "skills": {"type": "array", "items": _SKILL_SCHEMA, "maxItems": _MAX_SKILLS_PER_LOCATION},
}
_PROJECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_PROJECT_PROPERTIES),
    "properties": _PROJECT_PROPERTIES,
}
_LANGUAGE_PROPERTIES: dict[str, Any] = {
    "name": {"type": "string", "minLength": 1, "maxLength": 100},
    "proficiency": {"type": ["string", "null"], "enum": [*_PROFICIENCIES, None]},
}
_PROFILE_PROPERTIES: dict[str, Any] = {
    "full_name": _TEXT_NULL,
    "professional_title": _TEXT_NULL,
    "professional_summary": {"type": ["string", "null"], "maxLength": _SUMMARY_CAP},
    "phone_number": {"type": ["string", "null"], "maxLength": 32},
    "location": {"type": ["string", "null"], "maxLength": _LINE_CAP},
    "contact_email": {"type": ["string", "null"], "maxLength": _EMAIL_CAP},
    "experiences": {"type": "array", "items": _EXPERIENCE_SCHEMA, "maxItems": _MAX_RECORDS},
    "educations": {"type": "array", "items": _EDUCATION_SCHEMA, "maxItems": _MAX_RECORDS},
    "projects": {"type": "array", "items": _PROJECT_SCHEMA, "maxItems": _MAX_RECORDS},
    "skills": {"type": "array", "items": _SKILL_SCHEMA, "maxItems": _MAX_SKILLS_PER_LOCATION},
    "languages": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": list(_LANGUAGE_PROPERTIES),
            "properties": _LANGUAGE_PROPERTIES,
        },
        "maxItems": _MAX_LANGUAGES,
    },
}
PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_PROFILE_PROPERTIES),
    "properties": _PROFILE_PROPERTIES,
}

_REQUIREMENT_PROPERTIES: dict[str, Any] = {
    **_SKILL_PROPERTIES,
    "classification": {"type": ["string", "null"], "enum": ["required", "preferred", None]},
}
_REQUIREMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_REQUIREMENT_PROPERTIES),
    "properties": _REQUIREMENT_PROPERTIES,
}
_POSTING_PROPERTIES: dict[str, Any] = {
    "company_name": {"type": ["string", "null"], "maxLength": _LINE_CAP},
    "company_website": {"type": ["string", "null"], "maxLength": 253},
    "role_title": {"type": ["string", "null"], "maxLength": _LINE_CAP},
    "job_description": {"type": ["string", "null"], "maxLength": _JOB_DESCRIPTION_CAP},
    "location": {"type": ["string", "null"], "maxLength": _LINE_CAP},
    "compensation": {"type": ["string", "null"], "maxLength": _LINE_CAP},
    "posting_url": {"type": ["string", "null"], "maxLength": _URL_CAP},
    "requirements": {"type": "array", "items": _REQUIREMENT_SCHEMA, "maxItems": _MAX_REQUIREMENTS},
}
POSTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(_POSTING_PROPERTIES),
    "properties": _POSTING_PROPERTIES,
}


def parse_profile_result(decoded: object, source_text: str) -> CandidateProfileExtraction:
    """Validate a decoded profile extraction against the local contract."""

    record = _object(decoded, _PROFILE_PROPERTIES)
    grounding = _grounded(source_text)
    return CandidateProfileExtraction(
        full_name=_text(record, "full_name", _TEXT_CAP),
        professional_title=_text(record, "professional_title", _TEXT_CAP),
        professional_summary=_text(record, "professional_summary", _SUMMARY_CAP),
        phone_number=_text(record, "phone_number", 32),
        location=_text(record, "location", _LINE_CAP),
        contact_email=_email(record),
        experiences=tuple(
            _experience(item, grounding, index)
            for index, item in enumerate(_array(record, "experiences", _MAX_RECORDS))
        ),
        educations=tuple(
            _education(item, grounding) for item in _array(record, "educations", _MAX_RECORDS)
        ),
        projects=tuple(
            _project(item, grounding, index)
            for index, item in enumerate(_array(record, "projects", _MAX_RECORDS))
        ),
        skills=tuple(
            _skill(item, grounding) for item in _array(record, "skills", _MAX_SKILLS_PER_LOCATION)
        ),
        languages=tuple(_language(item) for item in _array(record, "languages", _MAX_LANGUAGES)),
        warnings=(),
    )


def parse_posting_result(decoded: object, source_text: str) -> JobPostingExtraction:
    """Validate a decoded posting extraction against the local contract."""

    record = _object(decoded, _POSTING_PROPERTIES)
    grounding = _grounded(source_text)
    warnings: list[str] = []
    requirements = tuple(
        _requirement(item, grounding, warnings)
        for item in _array(record, "requirements", _MAX_REQUIREMENTS)
    )
    return JobPostingExtraction(
        company_name=_text(record, "company_name", _LINE_CAP),
        company_website=_text(record, "company_website", 253),
        role_title=_text(record, "role_title", _LINE_CAP),
        job_description=_text(record, "job_description", _JOB_DESCRIPTION_CAP),
        location=_text(record, "location", _LINE_CAP),
        compensation=_text(record, "compensation", _LINE_CAP),
        posting_url=_url(record, "posting_url"),
        requirements=requirements,
        warnings=tuple(warnings),
    )


def _object(decoded: object, properties: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(decoded, dict):
        raise ResultRejected("result is not an object")
    expected = set(properties)
    if set(decoded) != expected:
        raise ResultRejected("result keys do not match the schema")
    return decoded


def _value(record: dict[str, Any], key: str) -> Any:
    if key not in record:
        raise ResultRejected(f"{key} is missing")
    return record[key]


def _text(record: dict[str, Any], key: str, cap: int) -> str | None:
    return _clean_text(_value(record, key), cap, key)


def _clean_text(value: Any, cap: int, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResultRejected(f"{label} is not a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > cap:
        raise ResultRejected(f"{label} exceeds its bound")
    return cleaned


def _email(record: dict[str, Any]) -> str | None:
    value = _text(record, "contact_email", _EMAIL_CAP)
    if value is None:
        return None
    if not _EMAIL_PATTERN.match(value):
        raise ResultRejected("contact_email is not an email address")
    return value


def _url(record: dict[str, Any], key: str) -> str | None:
    value = _text(record, key, _URL_CAP)
    if value is None:
        return None
    if not _URL_PATTERN.match(value):
        raise ResultRejected(f"{key} is not an HTTP(S) URL")
    return value


def _date(record: dict[str, Any], key: str) -> ExtractedDate | None:
    """Parse a date at exactly the precision the source stated."""

    value = _text(record, key, 10)
    if value is None:
        return None
    match = _DATE_PATTERN.match(value)
    if not match:
        raise ResultRejected(f"{key} is not a supported date precision")
    year = int(value[0:4])
    month = int(value[5:7]) if len(value) >= 7 else None
    day = int(value[8:10]) if len(value) == 10 else None
    if not 1 <= year <= 9999 or (month is not None and not 1 <= month <= 12):
        raise ResultRejected(f"{key} is not a real date")
    if day is not None:
        try:
            date(year, month or 1, day)
        except ValueError:
            raise ResultRejected(f"{key} is not a real date") from None
    return ExtractedDate(text=value, year=year, month=month, day=day)


def _bool(record: dict[str, Any], key: str) -> bool:
    value = _value(record, key)
    if not isinstance(value, bool):
        raise ResultRejected(f"{key} is not a boolean")
    return value


def _array(record: dict[str, Any], key: str, cap: int) -> list[Any]:
    value = _value(record, key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ResultRejected(f"{key} is not an array")
    if len(value) > cap:
        raise ResultRejected(f"{key} exceeds its bound")
    return value


def _enum(value: Any, key: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ResultRejected(f"{key} is not an allowed value")
    return value


def _skill_item(
    record: dict[str, Any], properties: dict[str, Any], grounding: Grounding
) -> dict[str, Any]:
    checked = _object(record, properties)
    wording = _text(checked, "source_wording", _TEXT_CAP)
    if wording is None or not grounding(wording):
        raise ResultRejected("skill wording is not grounded in the source")
    concept = _text(checked, "proposed_concept", _TEXT_CAP)
    status = _enum(checked["status"], "status", _STATUSES)
    if concept is None and status == "resolved":
        raise ResultRejected("a resolved proposal needs a proposed concept")
    return checked


def _skill(record: dict[str, Any], grounding: Grounding) -> SkillProposal:
    checked = _skill_item(record, _SKILL_PROPERTIES, grounding)
    return SkillProposal(
        source_wording=checked["source_wording"].strip(),
        proposed_concept=_text(checked, "proposed_concept", _TEXT_CAP),
        suitability=_enum(checked["suitability"], "suitability", _SUITABILITIES),
        status=_enum(checked["status"], "status", _STATUSES),
    )


def _requirement(
    record: dict[str, Any], grounding: Grounding, warnings: list[str]
) -> RequirementProposal:
    checked = _skill_item(record, _REQUIREMENT_PROPERTIES, grounding)
    classification = checked["classification"]
    if classification is not None:
        _enum(classification, "classification", ("required", "preferred"))
    else:
        warnings.append("unclassified_requirement")
    return RequirementProposal(
        source_wording=checked["source_wording"].strip(),
        proposed_concept=_text(checked, "proposed_concept", _TEXT_CAP),
        suitability=_enum(checked["suitability"], "suitability", _SUITABILITIES),
        status=_enum(checked["status"], "status", _STATUSES),
        classification=classification,
    )


def _date_warnings(
    start: ExtractedDate | None, end: ExtractedDate | None, is_current: bool
) -> tuple[str, ...]:
    warnings: list[str] = []
    if start is None:
        warnings.append("missing_start_date")
    elif not start.is_complete:
        warnings.append("incomplete_start_date")
    if end is None and not is_current:
        warnings.append("unresolved_end_date")
    elif end is not None and not end.is_complete:
        warnings.append("incomplete_end_date")
    return tuple(warnings)


def _experience(record: dict[str, Any], grounding: Grounding, index: int) -> ExperienceExtraction:
    checked = _object(record, _EXPERIENCE_PROPERTIES)
    start = _date(checked, "start_date")
    end = _date(checked, "end_date")
    is_current = _bool(checked, "is_current")
    return ExperienceExtraction(
        role=_text(checked, "role", _TEXT_CAP),
        organization=_text(checked, "organization", _TEXT_CAP),
        location=_text(checked, "location", _TEXT_CAP),
        start_date=start,
        end_date=end,
        is_current=is_current,
        description=_text(checked, "description", _SECTION_TEXT_CAP),
        highlights=tuple(
            highlight
            for highlight in (
                _clean_text(item, _HIGHLIGHT_CAP, "highlights")
                for item in _array(checked, "highlights", _MAX_HIGHLIGHTS)
            )
            if highlight is not None
        ),
        skills=tuple(
            _skill(item, grounding) for item in _array(checked, "skills", _MAX_SKILLS_PER_LOCATION)
        ),
        warnings=_date_warnings(start, end, is_current),
    )


def _education(record: dict[str, Any], grounding: Grounding) -> EducationExtraction:
    checked = _object(record, _EDUCATION_PROPERTIES)
    start = _date(checked, "start_date")
    end = _date(checked, "end_date")
    is_current = _bool(checked, "is_current")
    return EducationExtraction(
        institution=_text(checked, "institution", _TEXT_CAP),
        degree=_text(checked, "degree", _TEXT_CAP),
        start_date=start,
        end_date=end,
        is_current=is_current,
        warnings=_date_warnings(start, end, is_current),
    )


def _project(record: dict[str, Any], grounding: Grounding, index: int) -> ProjectExtraction:
    checked = _object(record, _PROJECT_PROPERTIES)
    return ProjectExtraction(
        name=_text(checked, "name", _TEXT_CAP),
        description=_text(checked, "description", _SECTION_TEXT_CAP),
        skills=tuple(
            _skill(item, grounding) for item in _array(checked, "skills", _MAX_SKILLS_PER_LOCATION)
        ),
        warnings=(),
    )


def _language(record: dict[str, Any]) -> LanguageExtraction:
    checked = _object(record, _LANGUAGE_PROPERTIES)
    name = _text(checked, "name", 100)
    if name is None:
        raise ResultRejected("language name is required")
    proficiency = checked["proficiency"]
    if proficiency is not None:
        _enum(proficiency, "proficiency", _PROFICIENCIES)
    return LanguageExtraction(name=name, proficiency=proficiency)


def _grounded(source_text: str) -> Grounding:
    haystack = unicodedata.normalize("NFC", source_text).casefold()

    def grounded(wording: str) -> bool:
        needle = unicodedata.normalize("NFC", wording).strip().casefold()
        return bool(needle) and needle in haystack

    return grounded
