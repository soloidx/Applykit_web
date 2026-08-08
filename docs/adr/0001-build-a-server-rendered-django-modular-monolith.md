# ADR-0001: Build a server-rendered Django modular monolith

- Status: Accepted
- Date: 2026-08-08

## Context

ApplyKit's first release is a cohesive tracking product owned by one team. It needs authentication, relational workflows, forms, dashboard updates, and browser-visible behavior, but it does not need independently deployed services or a separate frontend application.

The project should remain simple while preserving domain boundaries for profiles, campaigns, applications, and later document capabilities.

## Decision

Build ApplyKit as a Django modular monolith with:

- root `manage.py`
- project wiring in `config/`
- domain apps under `apps/`
- initial apps named `accounts`, `profiles`, `campaigns`, and `applications`
- Django templates for server-rendered HTML
- HTMX for focused partial-page interactions
- Tailwind CSS compiled through a minimal npm CLI setup
- no client-side application framework

Do not scaffold `resumes` or `cover_letters` until those capabilities enter an implementation scope.

## Consequences

- Authentication, forms, authorization, and rendering use Django's established request lifecycle.
- Domain boundaries are represented in modules, not network services.
- HTMX endpoints must behave as understandable HTTP operations and should keep non-JavaScript fallbacks where practical.
- Tailwind adds Node-based build tooling even though application behavior remains Python-centric.
- A separate API or frontend can be introduced later only when a concrete consumer requires it.
