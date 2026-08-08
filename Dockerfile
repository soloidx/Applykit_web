FROM node:24-slim AS assets

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY static/src ./static/src
RUN npm run build:css && rm -rf static/src

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY apps ./apps
COPY config ./config
COPY manage.py ./

RUN uv sync --frozen --no-dev

COPY templates ./templates
COPY --from=assets /app/static ./static

RUN DJANGO_SETTINGS_MODULE=config.settings.production \
    DJANGO_SECRET_KEY=build-only-secret-that-is-long-enough-for-production-checks-123456 \
    DJANGO_DEBUG=false \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgresql://build:build@localhost:5432/applykit \
    RESEND_API_KEY=build-only-key \
    DEFAULT_FROM_EMAIL=build@example.com \
    uv run --no-sync python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
