# ADR-0002: Use environment-specific Django settings and container boundaries

- Status: Accepted
- Date: 2026-08-08

## Context

ApplyKit needs three ways to run:

- direct local development with UV and SQLite
- containerized local development with Docker Compose and PostgreSQL
- production on a generic container platform with externally supplied PostgreSQL and secrets

Duplicating settings for direct and Docker development would allow the configurations to drift. Production must remain stateless and should not assume that Docker Compose, TLS termination, or PostgreSQL runs inside the application container.

## Decision

Use current mutually supported stable Python and Django releases at implementation time. Pin the selected Python runtime in `.python-version` and lock Python dependencies with UV.

Use Django settings modules named `base`, `development`, `test`, and `production`:

- direct and Docker development both use `development`
- environment variables select SQLite or PostgreSQL and other runtime dependencies
- `test` supports PostgreSQL for database-backed suites
- `production` requires secure values and fails fast when required configuration is absent

Use `django-environ` for typed environment parsing and database URLs. Commit example environment files, never real secrets.

Use Django's console email backend in both development modes. Use `django-anymail` with its Resend backend in production.

Build one stateless production web image for a generic container platform. The platform supplies PostgreSQL, TLS termination, secrets, and persistent infrastructure. Serve static application assets from the web image using an appropriate Django static-file strategy; user-uploaded media, if later introduced, must use external object storage.

## Consequences

- Direct SQLite development is convenient but does not prove PostgreSQL compatibility.
- Docker development provides production-like database behavior without maintaining separate Django settings.
- Production does not depend on a production Compose file.
- Resend-specific credentials stay at the email infrastructure boundary behind Django's email API.
- Database migrations must run as an explicit deployment step, not automatically in every web process.
