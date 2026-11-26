# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/). The project is in early development; version
`0.1.0` was the first release candidate. The version number lives in `dataroom/__init__.py`.

## [Unreleased]

## [0.2.0] - 2026-11-26

### Upgrading from 0.1.0
- Take a backup first.
- Set `AUDIT_CHAIN_KEY` in `.env` **before** running `migrate`, and keep it apart from the database. If it stays
  empty, the key is derived from `SECRET_KEY`. Changing the key later makes older audit entries fail the check.
- Run `migrate`: it adds groups and the audit chain, and seals the existing audit entries (marked `legacy`).
- In rooms with the watermark on, visitors now receive stamped copies of downloaded PDF files.

### Added
- Tamper-evident audit log: every entry has a number and an HMAC-SHA256 signature chained to the previous one
  (`AUDIT_CHAIN_KEY`, falls back to a key derived from `SECRET_KEY`). `verify_audit` and `audit_anchor` commands,
  a check and an anchor download on the audit screen, `seq` and `chain_mac` in the CSV export, events
  `audit.verified` and `audit.anchor_issued`. Existing entries are sealed by the migration and marked `legacy`.
- Stamped PDF downloads: with the watermark on, a visitor's download carries the stamp (email, time, address, code)
  on every page as vector text; built in the sandboxed worker, kept 15 minutes for resumed downloads; the code is
  written to the audit event `document.downloaded`. Administrators get the original. DOCX and XLSX are unchanged.
- Groups of visitors inside a room; access can be granted to a group, and a person's rights are the union of their
  own grants and their groups' grants. Access matrix (rows: room and folders; columns: groups and people) with
  inherited access shown. Read-only "View as" for an administrator, audited. An invitation can add the person to a
  group. Audit events `group.*` and `access.view_as_opened`.

### Changed
- `AuditEvent` gains `seq`, `actor_ref`, `legacy`, `chain_mac` and a new table holds the chain head
  (migration `audit.0003_audit_chain`, which also seals the existing entries).
- `AccessGrant.user` may be empty when `AccessGrant.group` is set; exactly one of them is required (database
  constraint). Migrations `rooms.0006_groups` and `invites.0002_groups`.

### Known limits
See `docs/ROADMAP.md` (per milestone) and `SECURITY.md`. In short: no independent security audit, no real SMTP
check, translations not reviewed by native speakers, phone layouts not checked on real devices; DOCX and XLSX are
downloaded without a stamp; a stamp can be painted over in a PDF editor; the audit chain cannot protect against
someone who holds both the database and the key unless an anchor line is kept elsewhere.

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
