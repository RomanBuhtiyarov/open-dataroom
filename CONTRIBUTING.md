# Contributing

Thank you for helping. The project is a self-hosted data room: one project, one instance, one Docker image.
Read `docs/ROADMAP.md` for scope and `README.md` for commands.

## Set up

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Linux and macOS: .venv/bin/pip
DJANGO_DEBUG=1 .venv/Scripts/python manage.py migrate
DJANGO_DEBUG=1 .venv/Scripts/python manage.py bootstrap_admin
DJANGO_DEBUG=1 .venv/Scripts/python manage.py runserver
```

`DJANGO_DEBUG=1` uses SQLite and a development secret key. Production needs PostgreSQL and real secrets, see
`docs/DEPLOYMENT.md`. `manage.py seed_demo` fills a development instance with synthetic data.

## Before you open a pull request

```bash
python -m pytest
DJANGO_DEBUG=1 python manage.py makemigrations --check --dry-run
python scripts/check_messages.py
python -m ruff check --select E9,F63,F7,F82,F401,F811 apps dataroom scripts tests
```

CI runs the same on SQLite and on PostgreSQL 16, builds the Docker image and runs a Compose smoke test with a
backup and restore. Tests that need PostgreSQL are skipped locally unless you set `TEST_DATABASE_URL`.

## Dependencies

`requirements.txt` holds version ranges; `requirements-lock.txt` holds the exact versions the Docker image installs
and CI audits. After changing `requirements.txt`, refresh the lock:

```bash
pip install pip-tools
pip-compile --strip-extras --no-header --no-annotate -o requirements-lock.txt requirements.txt
```

## Rules

- Every access check goes through `apps/rooms/policy.py` (or `apps/qa/services.py` for questions). A new view that
  returns data or a file needs a test that a user without access gets `404`.
- No tokens, passwords, codes or file contents in logs or in the audit log. The audit log stores event codes and ids.
- All user-visible text (templates, emails, errors, JavaScript) goes through gettext in `ru`, `uk`, `de` and `en`.
  Add the English string in code, then fill the catalogues in `locale/<code>/LC_MESSAGES/django.po`;
  `scripts/check_messages.py` lists what is missing and `scripts/compile_messages.py` compiles the catalogues.
- Use synthetic data in tests and demos. No real names, brands or documents.
- Secrets only from the environment. Never commit `.env`.
- Send emails inside `transaction.on_commit`.

## Translations

Native-speaker review of Russian, Ukrainian and German is especially welcome: edit the `.po` file and open a pull
request. Source strings are English.

## Reporting security problems

Not here. See `SECURITY.md`.
