## ApplyKit

ApplyKit is a server-rendered Django application for keeping a focused job search in one private workspace.

### Local development

Python 3.14 and [UV](https://docs.astral.sh/uv/) are required. The direct development settings use SQLite by default.

```sh
uv sync
cp .env.example .env
uv run python manage.py migrate
npm install
npm run build:css
uv run python manage.py runserver
```

Open `http://127.0.0.1:8000/`. Authentication email is printed to the console during development.

Run the quality checks with:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy apps config manage.py
uv run pytest tests/unit
uv run pytest tests/integration
DJANGO_ALLOW_ASYNC_UNSAFE=true uv run pytest -m browser
```

The browser suite requires Chromium once:

```sh
uv run playwright install chromium
```

### Docker development

Docker Compose runs the same `config.settings.development` module against PostgreSQL:

```sh
docker compose up --build -d db
docker compose run --rm web uv run python manage.py migrate
docker compose up --build
```

The web service is available at `http://127.0.0.1:8000/`.

The migration command is intentionally explicit; the web process does not race migrations during startup.

Production uses `config.settings.production`, requires PostgreSQL and secure environment values, and expects migrations to run as an explicit deployment step before the web process starts.
