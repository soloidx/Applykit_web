import json

import pytest

from apps.ai.schemas import (
    POSTING_SCHEMA,
    PROFILE_SCHEMA,
    ResultRejected,
    parse_posting_result,
    parse_profile_result,
)

pytestmark = pytest.mark.unit

SOURCE = "Jane Doe worked at Acme as a Senior Engineer. Skilled in Node.js and Rust."


def profile_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "full_name": None,
        "professional_title": None,
        "professional_summary": None,
        "phone_number": None,
        "location": None,
        "contact_email": None,
        "experiences": [],
        "educations": [],
        "projects": [],
        "skills": [],
        "languages": [],
    }
    payload.update(overrides)
    return payload


def posting_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "company_name": None,
        "company_website": None,
        "role_title": None,
        "job_description": None,
        "location": None,
        "compensation": None,
        "posting_url": None,
        "requirements": [],
    }
    payload.update(overrides)
    return payload


def skill(**overrides: object) -> dict[str, object]:
    proposal: dict[str, object] = {
        "source_wording": "Node.js",
        "proposed_concept": "Node.js",
        "suitability": "suitable",
        "status": "resolved",
    }
    proposal.update(overrides)
    return proposal


class TestStrictSchemaInvariants:
    def walk_objects(self, node: object) -> list[dict[str, object]]:
        found: list[dict[str, object]] = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for value in node.values():
                found.extend(self.walk_objects(value))
        elif isinstance(node, list):
            for value in node:
                found.extend(self.walk_objects(value))
        return found

    @pytest.mark.parametrize("schema", [PROFILE_SCHEMA, POSTING_SCHEMA])
    def test_every_object_is_closed_and_fully_required(self, schema: dict[str, object]) -> None:
        for node in self.walk_objects(schema):
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])


class TestProfileValidation:
    def test_minimal_empty_result_is_valid(self) -> None:
        extraction = parse_profile_result(profile_payload(), SOURCE)

        assert extraction.full_name is None
        assert extraction.warnings == ()
        assert not extraction.has_meaningful_facts()

    def test_root_facts_are_trimmed_and_blank_becomes_null(self) -> None:
        extraction = parse_profile_result(
            profile_payload(full_name="  Jane Doe  ", phone_number="   "), SOURCE
        )

        assert extraction.full_name == "Jane Doe"
        assert extraction.phone_number is None
        assert extraction.has_meaningful_facts()

    def test_exact_source_wording_is_grounded(self) -> None:
        extraction = parse_profile_result(profile_payload(skills=[skill()]), SOURCE)

        assert extraction.skills[0].source_wording == "Node.js"

    def test_grounding_is_case_insensitive(self) -> None:
        extraction = parse_profile_result(
            profile_payload(skills=[skill(source_wording="node.JS")]), SOURCE
        )

        assert extraction.skills[0].status == "resolved"

    def test_ungrounded_wording_is_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(
                profile_payload(skills=[skill(source_wording="Kubernetes")]), SOURCE
            )

    def test_unsuitable_status_combinations(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(
                profile_payload(skills=[skill(status="resolved", proposed_concept=None)]),
                SOURCE,
            )

    def test_unknown_status_may_omit_concept(self) -> None:
        extraction = parse_profile_result(
            profile_payload(skills=[skill(status="unknown", proposed_concept=None)]), SOURCE
        )

        assert extraction.skills[0].proposed_concept is None

    def test_missing_and_extra_keys_are_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(
                {k: v for k, v in profile_payload().items() if k != "skills"}, SOURCE
            )
        extra = profile_payload()
        extra["invented_field"] = "nope"
        with pytest.raises(ResultRejected):
            parse_profile_result(extra, SOURCE)

    def test_non_object_result_is_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(["nope"], SOURCE)

    def test_overlong_strings_are_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(profile_payload(full_name="x" * 201), SOURCE)

    def test_invalid_email_is_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(profile_payload(contact_email="not-an-email"), SOURCE)

    def test_valid_email_is_kept(self) -> None:
        extraction = parse_profile_result(profile_payload(contact_email="jane@example.com"), SOURCE)

        assert extraction.contact_email == "jane@example.com"

    def test_experience_round_trip(self) -> None:
        experience = {
            "role": "Senior Engineer",
            "organization": "Acme",
            "location": "Remote",
            "start_date": "2021-03-01",
            "end_date": "2024-01-15",
            "is_current": False,
            "description": "Built systems.",
            "highlights": ["Shipped the platform."],
            "skills": [skill()],
        }
        extraction = parse_profile_result(profile_payload(experiences=[experience]), SOURCE)

        record = extraction.experiences[0]
        assert record.start_date is not None
        assert record.start_date.text == "2021-03-01"
        assert record.start_date.is_complete
        assert record.end_date is not None
        assert record.end_date.text == "2024-01-15"
        assert record.warnings == ()
        assert record.skills[0].source_wording == "Node.js"

    def test_partial_dates_are_retained_at_source_precision(self) -> None:
        experience = {
            "role": "Senior Engineer",
            "organization": "Acme",
            "location": None,
            "start_date": "2021-03",
            "end_date": None,
            "is_current": False,
            "description": None,
            "highlights": [],
            "skills": [],
        }
        extraction = parse_profile_result(profile_payload(experiences=[experience]), SOURCE)

        record = extraction.experiences[0]
        assert record.start_date is not None
        assert record.start_date.text == "2021-03"
        assert not record.start_date.is_complete
        assert "incomplete_start_date" in record.warnings

    def test_impossible_dates_are_rejected(self) -> None:
        base = {
            "role": "Senior Engineer",
            "organization": "Acme",
            "location": None,
            "start_date": "2021-03-01",
            "end_date": None,
            "is_current": False,
            "description": None,
            "highlights": [],
            "skills": [],
        }
        for bad_date in ("2021-13-01", "2021-03-32", "not-a-date", "01-02-2021"):
            with pytest.raises(ResultRejected):
                parse_profile_result(
                    profile_payload(experiences=[{**base, "start_date": bad_date}]), SOURCE
                )

    def test_missing_dates_become_typed_warnings(self) -> None:
        experience = {
            "role": "Senior Engineer",
            "organization": "Acme",
            "location": None,
            "start_date": None,
            "end_date": None,
            "is_current": False,
            "description": None,
            "highlights": [],
            "skills": [],
        }
        extraction = parse_profile_result(profile_payload(experiences=[experience]), SOURCE)

        assert extraction.experiences[0].warnings == (
            "missing_start_date",
            "unresolved_end_date",
        )

    def test_explicit_current_role_has_no_end_date_warning(self) -> None:
        experience = {
            "role": "Senior Engineer",
            "organization": "Acme",
            "location": None,
            "start_date": "2021-03-01",
            "end_date": None,
            "is_current": True,
            "description": None,
            "highlights": [],
            "skills": [],
        }
        extraction = parse_profile_result(profile_payload(experiences=[experience]), SOURCE)

        assert extraction.experiences[0].warnings == ()

    def test_education_and_project_round_trip(self) -> None:
        education = {
            "institution": "State University",
            "degree": "BSc Computer Science",
            "start_date": "2013-09-01",
            "end_date": "2017-06-30",
            "is_current": False,
        }
        project = {
            "name": "Parser",
            "description": "A parser.",
            "skills": [skill(source_wording="Rust", proposed_concept="Rust")],
        }
        extraction = parse_profile_result(
            profile_payload(educations=[education], projects=[project]), SOURCE
        )

        assert extraction.educations[0].institution == "State University"
        assert extraction.projects[0].skills[0].source_wording == "Rust"

    def test_language_validation(self) -> None:
        languages = [
            {"name": "Spanish", "proficiency": "fluent"},
            {"name": "German", "proficiency": None},
        ]
        extraction = parse_profile_result(profile_payload(languages=languages), SOURCE)

        assert extraction.languages[0].proficiency == "fluent"
        assert extraction.languages[1].proficiency is None

    def test_invalid_language_is_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(
                profile_payload(languages=[{"name": "Spanish", "proficiency": "native-plus"}]),
                SOURCE,
            )
        with pytest.raises(ResultRejected):
            parse_profile_result(
                profile_payload(languages=[{"name": "", "proficiency": None}]), SOURCE
            )

    def test_order_is_preserved(self) -> None:
        first = {
            "role": "Senior Engineer",
            "organization": "Acme",
            "location": None,
            "start_date": "2021-03-01",
            "end_date": None,
            "is_current": True,
            "description": None,
            "highlights": [],
            "skills": [],
        }
        second = {**first, "role": "Junior Engineer", "organization": "Beta"}
        extraction = parse_profile_result(profile_payload(experiences=[first, second]), SOURCE)

        assert [record.role for record in extraction.experiences] == [
            "Senior Engineer",
            "Junior Engineer",
        ]

    def test_record_bounds_are_enforced(self) -> None:
        with pytest.raises(ResultRejected):
            parse_profile_result(profile_payload(experiences=[{} for _ in range(31)]), SOURCE)

    def test_real_json_round_trip(self) -> None:
        payload = profile_payload(full_name="Jane Doe")
        extraction = parse_profile_result(json.loads(json.dumps(payload)), SOURCE)
        assert extraction.full_name == "Jane Doe"


class TestPostingValidation:
    def test_full_posting_round_trip(self) -> None:
        payload = posting_payload(
            company_name="Acme",
            company_website="https://acme.example.com",
            role_title="Backend Engineer",
            job_description="Build distributed systems with Node.js.",
            location="Remote",
            compensation="$120k",
            posting_url="https://jobs.example.com/42",
            requirements=[
                {
                    "source_wording": "Node.js",
                    "proposed_concept": "Node.js",
                    "suitability": "suitable",
                    "status": "resolved",
                    "classification": "required",
                },
                {
                    "source_wording": "Rust",
                    "proposed_concept": None,
                    "suitability": "suitable",
                    "status": "ambiguous",
                    "classification": None,
                },
            ],
        )
        source = (
            "Acme (https://acme.example.com) is hiring a Backend Engineer. "
            "Build distributed systems with Node.js. Remote. $120k. "
            "https://jobs.example.com/42 Required: Node.js. Rust."
        )
        extraction = parse_posting_result(payload, source)

        assert extraction.company_name == "Acme"
        assert extraction.role_title == "Backend Engineer"
        assert extraction.requirements[0].classification == "required"
        assert extraction.requirements[1].classification is None
        assert extraction.warnings == ("unclassified_requirement",)
        assert extraction.has_meaningful_facts()

    def test_empty_posting_result_is_not_meaningful(self) -> None:
        extraction = parse_posting_result(posting_payload(), "nothing relevant")
        assert not extraction.has_meaningful_facts()

    def test_invented_requirement_wording_is_rejected(self) -> None:
        payload = posting_payload(
            requirements=[
                {
                    "source_wording": "COBOL",
                    "proposed_concept": "COBOL",
                    "suitability": "suitable",
                    "status": "resolved",
                    "classification": "required",
                }
            ]
        )
        with pytest.raises(ResultRejected):
            parse_posting_result(payload, "Backend Engineer wanted.")

    def test_invalid_classification_is_rejected(self) -> None:
        payload = posting_payload(
            requirements=[
                {
                    "source_wording": "Node.js",
                    "proposed_concept": None,
                    "suitability": "suitable",
                    "status": "unknown",
                    "classification": "mandatory",
                }
            ]
        )
        with pytest.raises(ResultRejected):
            parse_posting_result(payload, SOURCE)

    def test_invalid_posting_url_is_rejected(self) -> None:
        with pytest.raises(ResultRejected):
            parse_posting_result(posting_payload(posting_url="not a url"), SOURCE)

    def test_valid_posting_url_with_length_bound(self) -> None:
        extraction = parse_posting_result(
            posting_payload(posting_url="https://jobs.example.com/a"), SOURCE
        )
        assert extraction.posting_url == "https://jobs.example.com/a"
        with pytest.raises(ResultRejected):
            parse_posting_result(posting_payload(posting_url="https://x" + "a" * 2048), SOURCE)
