# ADR-0004: Separate test purposes and CI costs

- Status: Accepted
- Date: 2026-08-08

## Context

ApplyKit needs fast feedback for domain logic, confidence in Django and PostgreSQL behavior, and browser coverage for critical server-rendered and HTMX journeys. Calling every non-browser test a unit test would hide database coupling. Running browsers on every pull request would increase feedback time beyond the desired initial CI budget.

## Decision

Use pytest with three explicit suites:

- `tests/unit/` for pure logic without Django database access
- `tests/integration/` for Django, ORM, authorization, forms, and PostgreSQL behavior
- `tests/browser/` for a small set of end-to-end journeys using pytest-playwright

Use PostgreSQL for integration and browser suites in CI. SQLite is only a direct-development convenience.

Use Ruff for formatting and linting. Use mypy with Django stubs for static type checks.

GitHub Actions pull requests run Ruff, mypy, and unit tests. Integration and browser suites run on pushes to `main` and by manual workflow dispatch.

## Consequences

- Test location communicates purpose and expected cost.
- Pull requests receive fast feedback but may merge before database or browser regressions are detected.
- The `main` workflow must remain visible and actionable when a full suite fails.
- Before a release, the full workflow must have passed for the release commit.
- Browser tests should cover only critical journeys rather than duplicating lower-level assertions.
