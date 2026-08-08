# ADR-0005: Defer asynchronous and generative infrastructure

- Status: Accepted
- Date: 2026-08-08

## Context

The original project outline included Celery with an in-memory backend for direct development and Redis for Docker and production. The agreed first release contains tracking, dashboard-visible events, and no email reminders. Resume generation, cover-letter generation, company research, and AI suggestions are deferred.

The first release therefore has no genuine asynchronous workload. Adding Celery and Redis now would increase configuration and operational surface without exercising them through product behavior.

## Decision

Do not include Celery, Redis, workers, schedulers, or task-backend settings in the initial project.

Do not create placeholder AI provider interfaces or empty `resumes` and `cover_letters` apps.

Introduce asynchronous infrastructure with the first feature that requires durable background execution. Before sending candidate or job-application data to an AI provider, decide and document:

- provider boundary and failure behavior
- explicit user consent
- fields sent and any redaction
- provider and application retention
- deletion and audit behavior
- whether generated outputs are snapshots or live derivatives

## Consequences

- Initial direct, Docker, test, and production configurations remain smaller.
- Recruitment events appear on the dashboard but do not send reminders.
- Future asynchronous work requires a new ADR selecting broker, result storage, retries, idempotency, scheduling, and observability.
- AI and document features cannot be treated as simple model additions because they introduce privacy and lifecycle decisions.
