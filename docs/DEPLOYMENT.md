# Deployment guide

One instance is one Docker Compose project: a web container, a PostgreSQL container and a private volume for documents. Everything is configured through `.env`.

> **Status.** The Compose files, the Nginx and Caddy examples and the PostgreSQL branch of backup and restore have not been run against a real Docker daemon or PostgreSQL yet. The SQLite branch and the archive logic are covered by tests. Treat the first deployment as a rehearsal and follow "Test the restore" below before you rely on it.

## 1. Requirements

- A Linux server with Docker Engine and the Compose plugin; 1 vCPU and 1 GB RAM are enough for a small room. Disk: the documents plus their backups.
- A domain or subdomain pointing at the server and a reverse proxy that terminates HTTPS.
- An SMTP account for invitations and codes.

## 2. First start

```bash
git clone https://github.com/RomanBuhtiyarov/open-dataroom.git
cd open-dataroom
cp .env.example .env        # fill in SECRET_KEY, PUBLIC_BASE_URL, passwords, SMTP
cd deploy
docker compose --env-file ../.env up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py bootstrap_admin
```

Open `PUBLIC_BASE_URL`, sign in, enrol the second factor and complete the first-launch screen.

`SECRET_KEY` can be generated with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Keep `.env` out of git and out of backups that leave your control; it holds every secret.

## 3. Reverse proxy

The web container listens on `127.0.0.1:${WEB_PORT:-8000}` only. Put one of these in front of it:

- [`deploy/nginx.example.conf`](../deploy/nginx.example.conf)
- [`deploy/Caddyfile.example`](../deploy/Caddyfile.example)

Both examples:

- redirect or serve HTTPS only;
- log the request path **without the query string** (invitation links carry a token in `?t=`);
- set the client address in `X-Forwarded-For` themselves, replacing any value sent by the client. The application trusts the last element when `TRUST_PROXY_HEADERS=1`. Never set that variable without such a proxy in front.
- allow request bodies slightly above `MAX_UPLOAD_BYTES` (55 MB for the default 50 MB).

`PUBLIC_BASE_URL` must match the public address exactly; the application accepts only that host.

## 4. Backups

A backup holds the database and the documents together in one archive: `backup_instance` takes the database first and the files after it, so every row in the copy points at a file in the archive. Preview images are not included; they are rebuilt on demand.

```bash
docker compose exec web python manage.py backup_instance /tmp/dataroom.tar.gz
docker compose cp web:/tmp/dataroom.tar.gz ./dataroom-$(date +%F).tar.gz
docker compose exec web rm /tmp/dataroom.tar.gz
```

Then encrypt the file and move it off the server, for example:

```bash
age -r <public-key> -o dataroom-2026-01-01.tar.gz.age dataroom-2026-01-01.tar.gz && shred -u dataroom-2026-01-01.tar.gz
```

What the archive contains and does not contain:

| In the archive | Not in the archive |
|---|---|
| All rows of the database, including password hashes and 2FA secrets | `.env`: `SECRET_KEY`, SMTP and database passwords |
| Every stored document version and the logo | The preview cache |
| A manifest with SHA-256 of each file, the engine and the migration state | The reverse proxy configuration and TLS certificates |

Because the archive holds confidential documents, it must be stored encrypted, with access limited to the people who may read the whole room. Keep a copy of `.env` separately: without the same `SECRET_KEY` sessions are invalidated, and the 2FA secrets in the database are still valid but you lose the deployment settings.

Schedule it with cron on the host, for example nightly, and keep several generations. Check that the file is non-empty and that the command exit code is 0; a failure writes nothing to the target path.

## 5. Restore

1. Start a clean instance of the **same or a newer** program version (`docker compose up -d`, then `migrate`). Do not run `bootstrap_admin`.
2. Copy the decrypted archive into the container and restore:

```bash
docker compose cp dataroom.tar.gz web:/tmp/dataroom.tar.gz
docker compose exec web python manage.py restore_instance /tmp/dataroom.tar.gz
docker compose exec web python manage.py migrate     # only if the program is newer than the backup
docker compose exec web rm /tmp/dataroom.tar.gz
```

The command first unpacks the whole archive into a temporary directory and verifies every checksum and path. Nothing in the instance changes if the archive is damaged, has unsafe paths, was made on another database engine or comes from a newer version of the program. It refuses to touch an instance that already has users unless you pass `--replace`, which overwrites the database with the copy. Files are written before the database, so a failure in between leaves unused files, never rows without files.

After the restore, sign in and open a document and the audit log. The audit log shows a "backup restored" event.

### Test the restore

Do this once after the first deployment and after each upgrade of the program: restore the latest backup into a second instance (section 6) on the same server, sign in, open a document from each room and compare the file with the original. A backup that has never been restored is not a backup.

## 6. Two instances on one server

Each instance needs its own project name, port, database, volume, secrets and domain:

```bash
cp .env.example ../.env.second       # different SECRET_KEY, PUBLIC_BASE_URL, passwords, cookie names
cd deploy
COMPOSE_PROJECT_NAME=dataroom2 WEB_PORT=8001 docker compose --env-file ../.env.second up -d --build
```

Isolation rests on these points:

- Compose prefixes volumes and networks with the project name, so `db_data` and `private_media` of two instances are different volumes and the database containers cannot reach each other.
- The database port is not published.
- Each application accepts only its own `PUBLIC_BASE_URL` host.
- Use different `SESSION_COOKIE_NAME` and `CSRF_COOKIE_NAME` when both instances share a parent domain, so one never reads the other's cookies. Cookies are host-only, so on different subdomains they are not shared in any case.
- Do not point two instances at the same `PRIVATE_MEDIA_ROOT` or the same database.

A test checks that two instances started from different environments get different hosts, cookie names and volume paths. It does not start containers; verify the live setup yourself by signing in to the first instance and confirming that the second does not recognise the session.

## 7. Upgrades

```bash
git pull
cd deploy
docker compose --env-file ../.env up -d --build
docker compose exec web python manage.py migrate
```

Take a backup first. There are no automatic downgrades: to go back, deploy the older version and restore the backup made before the upgrade.

## 8. Logs

Gunicorn and the proxy log paths without query strings, and the application never logs tokens, passwords, codes or file contents. If you add your own log shipping, keep it that way: do not enable debug logging of requests or bodies.

## 9. Known limits

- Backups are manual or cron-driven; there is no built-in scheduler, encryption or retention policy.
- The archive is read into a temporary directory on restore, so you need free disk space equal to the size of the archive's contents.
- A backup made while a user uploads can contain files without rows; they are unused and harmless. The opposite case matters: deleting a document while a backup runs can leave a row whose file is not in the archive. Do not delete documents during a backup.
- Restoring into PostgreSQL needs `pg_restore` of the same or a newer major version than the server that made the dump; the image ships the client from Debian trixie.
- Emails that fail to send are recorded and can be resent by an administrator; there is no background retry queue.
