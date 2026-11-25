# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). The project is in early development; version
`0.1.0` is the first release candidate. The version number lives in `dataroom/__init__.py`.

## [0.1.0] - 2026-11-25

### Added
- Project skeleton: Django 5.2, PostgreSQL 16 in production (SQLite for development and tests), Docker image and
  Compose, four interface languages (ru, uk, de, en).
- First launch: `bootstrap_admin`, TOTP two-factor authentication for administrators, instance profile and language.
- Rooms, folder tree, documents with immutable versions, access grants on rooms and folders with expiry and a
  download right, one central access policy, room templates, private file storage.
- Personal invitations by link or QR code, email confirmation by a one-time code, self-registration with
  administrator approval, translated emails, database rate limits.
- Administrator and visitor screens, audit log with event codes and CSV export, raster page previews for view-only
  documents, download by permission, immediate effect of revocation, NDA with versions.
- Backup and restore (database and documents in one checksummed archive), Nginx and Caddy examples, a second
  instance from the same image, deployment guide.
- Password reset by a one-time link, `no-store` on token pages, request id in the audit log.
- Move folders and documents, delete documents, starter rooms, PDF parsing in a separate sandboxed process.
- Dynamic watermark on preview pages (email, time, address, view code) and view analytics (time per page,
  unopened documents, activity per day, CSV).
- Questions and answers: visitor questions on a room, folder or document, administrator inbox with assignee and
  due date, answers with an attachment, optional anonymous sharing, email notices without content, XLSX export.
- CI on GitHub Actions: tests on SQLite and PostgreSQL 16, lint, Docker build and a Compose smoke test with backup
  and restore, dependency audit, Dependabot.
- `seed_demo` command with synthetic demo data (development only).
- Content-Security-Policy, a 12-hour session lifetime, exact dependency versions (`requirements-lock.txt`),
  `SECURITY.md`, `CONTRIBUTING.md`.

### Fixed
- The Docker image did not build (a broken multi-line `CMD`) and carried the PostgreSQL 17 client, so restoring a
  backup into PostgreSQL 16 failed; the image now installs `postgresql-client-16`.
- Deleting a folder could widen a shared answer to the whole room; unusual digits in request parameters caused a
  server error; two simultaneous first answers could send two emails; a saved due date was not displayed in the
  ru, uk and de interface; uncached page renders are now rate limited.

### Known limits
See `docs/ROADMAP.md` (per milestone) and `SECURITY.md`. In short: no independent security audit, no real SMTP
check, translations not reviewed by native speakers, downloads are not watermarked, the audit log is not
tamper-evident.
