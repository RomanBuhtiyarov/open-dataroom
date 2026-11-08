# Open Data Room

A self-hosted, open-source data room for investors, due diligence and document sharing.
One project, one instance, one Docker image. Interface in Russian, Ukrainian, German and English.

> **Status: early development.** Milestone 0 (project skeleton) is done. See the [roadmap](docs/ROADMAP.md) ([на русском](docs/ROADMAP.ru.md)).
> No license has been chosen yet, so all rights are reserved until one is added.

## What works today

- Django project configured entirely from environment variables.
- Four languages (ru, uk, de, en) with a switcher that keeps typed values on screen.
- User model and email/password sign-in page.
- Dockerfile and Compose file (not yet exercised in CI).

Rooms, invitations, two-factor authentication and the audit log are on the roadmap.

## Local development (no Docker)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/python
DJANGO_DEBUG=1 .venv/Scripts/python manage.py migrate
DJANGO_DEBUG=1 .venv/Scripts/python manage.py runserver localhost:8000
```

Open `http://localhost:8000/`. The app only accepts the host from `PUBLIC_BASE_URL`; other hosts are rejected.
Without `DATABASE_URL`, `DJANGO_DEBUG=1` uses SQLite. Production requires PostgreSQL.

## Tests

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python manage.py makemigrations --check --dry-run
```

Tests run on SQLite. PostgreSQL coverage will be added with CI.

## Translations

Source strings are English. Catalogues: `locale/{ru,uk,de}/LC_MESSAGES/django.po`.
Use `{% translate %}` / `gettext` for new strings, then update the catalogues (`manage.py makemessages -a`, requires GNU gettext).
Without gettext: `python scripts/compile_messages.py` compiles `.po` to `.mo`. The Docker image compiles them at build time.

## Deployment (Compose)

```bash
cp .env.example .env        # set SECRET_KEY, PUBLIC_BASE_URL and database password
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml run --rm web python manage.py migrate
```

The web port is published on `127.0.0.1` only; put a TLS-terminating reverse proxy in front (see `deploy/nginx.example.conf`).

## Contributing

Issues and pull requests are welcome, especially native-speaker review of translations. Never commit secrets or real documents; use synthetic data in tests and demos.
