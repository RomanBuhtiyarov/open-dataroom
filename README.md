# Open Data Room

A self-hosted, open-source data room for investors, due diligence and document sharing.
One project, one instance, one Docker image. Interface in Russian, Ukrainian, German and English.

> **Status: early development.** Milestones 0 (skeleton) and 1 (first launch and administrators) are done. See the [roadmap](docs/ROADMAP.md) ([на русском](docs/ROADMAP.ru.md)).
> Licensed under the [GNU Affero General Public License v3.0](LICENSE).

## What works today

- Django project configured entirely from environment variables.
- Four languages (ru, uk, de, en) with a switcher that keeps typed values on screen.
- User model and email/password sign-in page.
- `bootstrap_admin`: creates the first administrator once; a second run is refused, also under concurrent calls.
- TOTP two-factor authentication is required for every administrator action; `reset_2fa` recovers a lost device (server access only).
- First-launch screen and instance settings: name, logo, default language, room template, self-registration switch.
- Profile page (name, interface language) and a minimal audit trail of these events (stored as event codes).
- Dockerfile and Compose file (not yet exercised in CI).

Rooms, documents, invitations and the audit screens are on the roadmap.

## Local development (no Docker)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/python
DJANGO_DEBUG=1 .venv/Scripts/python manage.py migrate
DJANGO_DEBUG=1 .venv/Scripts/python manage.py runserver localhost:8000
```

Create the first administrator (asks for the password interactively; it is never taken from arguments or the environment):

```bash
DJANGO_DEBUG=1 .venv/Scripts/python manage.py bootstrap_admin
```

Open `http://localhost:8000/`, sign in, scan the QR code with an authenticator app, then fill in the first-launch screen.
Lost the authenticator? Run `manage.py reset_2fa <email>` on the server; the user enrols a new device at the next sign-in.

The app only accepts the host from `PUBLIC_BASE_URL`; other hosts are rejected.
Without `DATABASE_URL`, `DJANGO_DEBUG=1` uses SQLite. Production requires PostgreSQL.

## Tests

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python manage.py makemigrations --check --dry-run
```

Tests run on SQLite. PostgreSQL coverage will be added with CI.
`python scripts/check_messages.py` fails if a string used in code or templates is missing from a catalogue.

## Translations

Source strings are English. Catalogues: `locale/{ru,uk,de}/LC_MESSAGES/django.po`.
Use `{% translate %}` / `gettext` for new strings, then update the catalogues (`manage.py makemessages -a`, requires GNU gettext).
Without gettext: `python scripts/compile_messages.py` compiles `.po` to `.mo`. The Docker image compiles them at build time.

## Deployment (Compose)

```bash
cp .env.example .env        # set SECRET_KEY, PUBLIC_BASE_URL and database password
docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml run --rm web python manage.py migrate
docker compose --env-file .env -f deploy/docker-compose.yml run --rm web python manage.py bootstrap_admin
```

The web port is published on `127.0.0.1` only; put a TLS-terminating reverse proxy in front (see `deploy/nginx.example.conf`).

## Contributing

Issues and pull requests are welcome, especially native-speaker review of translations. Never commit secrets or real documents; use synthetic data in tests and demos.

## License

GNU AGPL version 3. If you run a modified version as a network service, you must offer its source to the users of that service. See [LICENSE](LICENSE).
