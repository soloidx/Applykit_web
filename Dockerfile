FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY apps ./apps
COPY config ./config
COPY manage.py ./

RUN uv sync --frozen --no-dev

COPY templates ./templates
COPY static ./static

RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=build-only-secret \
    DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=sqlite:///build.sqlite3 \
    RESEND_API_KEY=build-only-key \
    DEFAULT_FROM_EMAIL=build@example.com \
    uv run python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["uv", "run", "gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
