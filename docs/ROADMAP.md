# Roadmap

A self-hosted, open-source data room for investors, due diligence and document sharing. One project, one instance, one Docker image.

Status legend: **done**, **next**, **planned**, **later**.

## Principles

- **Closed by default.** Access is computed on every request from one central policy. List, preview, download, range requests and direct URLs follow the same rules.
- **One instance per project.** Separate database, secrets, storage volume, cookies and backups. The same image runs for any number of independent instances.
- **Multilingual from day one.** Russian, Ukrainian, German and English. Every screen, form error and email is translated.
- **Small footprint.** Django, PostgreSQL, server-rendered templates, a little JavaScript. No required external services.
- **Auditable.** Every sensitive action is recorded with a stable event code. Secrets, tokens and file contents are never logged.

## Milestones

| # | Milestone | Status | Outcome |
|---|---|---|---|
| 0 | Project skeleton | **done** | Django project, settings from environment, Docker image and Compose, four languages with a language switcher, user model, sign-in page |
| 1 | First launch and administrators | **done** | `bootstrap_admin` command (runs once, safe against concurrent calls), TOTP two-factor authentication for administrators, first-launch screen, instance profile and default language |
| 2 | Rooms, documents and access policy | **done** | Rooms, folder tree, documents and versions, access grants, central access policy, room templates, private file storage |
| 3 | Invitations and registration | **done** | Personal invitation by link or QR code, email verification by one-time code, self-registration with administrator approval, translated emails, rate limits |
| 4 | Screens, audit and previews | **done** | Administrator and investor screens, audit log, raster page previews for view-only documents, download by permission, revocation takes effect immediately |
| 5 | Operations | done | Second instance from the same image, backup and restore procedure, deployment guide with reverse proxy examples |
| 6 | Specification gaps: security | done | Password reset by one-time link, no-store on token pages, request id in the audit log |
| 7 | Specification gaps: content | done | Move folders and documents, delete documents, starter rooms, PDF parsing in a separate process |
| 8 | Watermarks and view analytics | done | Per-room watermark on preview pages (email, time, address, view code), time per page, unopened documents, activity per day, CSV |
| 9 | Release readiness | done | CI on GitHub Actions: tests on SQLite and PostgreSQL 16, lint, Docker image build and a Compose smoke test with backup and restore |
| 10 | Questions and answers | done | Visitor questions on a room, folder or document; administrator inbox with status, assignee and due date; answers with an attachment; optional anonymous sharing; email notices; XLSX export |
| 11 | Release candidate | done | Browser review of administrator screens and phone width, security review and fixes, CSP, session lifetime, locked and audited dependencies, Dependabot, SECURITY.md, CONTRIBUTING.md, CHANGELOG.md |

### 0. Project skeleton (done)

- Settings read from the environment; only the configured host is accepted; cookies are host-only, `Secure`, `HttpOnly`, `SameSite`.
- Language order: browser choice, then the signed-in user's preference, then the instance language; only the four supported codes are accepted.
- Language switch keeps the current screen and typed values, including passwords and codes, in memory only.
- Custom user model with a normalised, case-insensitive unique email.

### 1. First launch and administrators (done)

- Interactive `bootstrap_admin`; the second run refuses with a clear message.
- Administrator actions require a second factor.
- First-launch screen: name, logo, default language, room template, self-registration switch.
- No public route can create an administrator.
- Known limits: no recovery codes (a lost device is reset from the server with `reset_2fa`); the audit trail has no screen yet (milestone 4).

### 2. Rooms, documents and access policy (done)

- Rooms and nested folders; an access scope on a folder includes its descendants.
- Documents with immutable versions; a new version is published only after the file is stored successfully.
- Access grants per user for a room or folders, with an optional download right and expiry; multiple grants combine.
- Optional NDA requirement per room, with versioned, per-language NDA texts and a recorded acceptance.
- PDF, DOCX and XLSX accepted after server-side checks of type, extension and size.
- Room templates that can be applied repeatedly without creating duplicates or resetting permissions.
- Known limits: originals are served whole (no HTTP Range, no page previews until milestone 4); visitors get the current version only; a document cannot be deleted or a folder moved yet; viewers are created with the `create_viewer` development helper until invitations exist.

### 3. Invitations and registration (done)

- Invitation links with opaque tokens stored only as hashes; opening the link never consumes it, submitting the form does, in one transaction.
- Ownership of the invited address confirmed by a one-time code.
- Self-registration with an approval queue; each request is decided once.
- Emails are sent after the transaction commits, in the recipient's language, and can be retried safely.
- Rate limits on sign-in, email sending and code checks.
- Known limits: no QR scanner screen of its own (the invitation page is mobile friendly); emails are sent right after commit with no retry queue (the administrator resends; the recipient can ask for a new code); invitations target viewers only, administrators are created with `bootstrap_admin`; rate limits count by client address, which needs `TRUST_PROXY_HEADERS` behind a proxy.

### 4. Screens, audit and previews (done)

- Screens for sign-in, invitation, registration, request status, administration, rooms, files, active access, audit log and document viewing.
- Audit events with UTC time, actor, event code, target and outcome.
- View-only documents are shown as page images through the same access checks; originals are available only with the download right.
- Mobile-friendly flow for scanning an invitation QR code.
- Known limits: previews exist for PDF only (DOCX and XLSX are download-only; a PDF copy can be uploaded as a version); rendering runs in the web process with page and size caps but no sandbox; no watermarks, so view-only is a deterrent, not a guarantee; the audit log is not tamper-evident yet (hash chain is on the post-release list); mobile layout was checked in a browser at 375 px, not on real devices.

### 5. Operations (done)

- Reverse proxy examples for Nginx and Caddy (`deploy/`), neither logging the query string.
- `backup_instance` and `restore_instance`: database and documents in one checksummed archive; the restore verifies everything before changing anything and refuses data-bearing instances without `--replace`.
- Two instances from one image: separate Compose project, port, database, volume, secrets and cookie names; a test checks that hosts, cookies and volume paths follow the environment.
- Deployment guide: [docs/DEPLOYMENT.md](DEPLOYMENT.md).
- Known limits: the Compose files, the Nginx and Caddy examples and the PostgreSQL branch of backup and restore have not been run against a real Docker daemon or PostgreSQL (the archive logic and the SQLite branch are tested); no built-in scheduler, encryption or retention for backups; failed emails have no background retry queue. See [docs/DEPLOYMENT.md](DEPLOYMENT.md).

### 6. Specification gaps: security (done)

- Password reset: `/password-reset/`. The same answer for known and unknown addresses; the link carries a signed, one-time token in `?t=` (opening it consumes nothing, the token moves into the session and out of the address bar); valid for one hour; a sign-in or a password change invalidates it; rate limited by address and by client; suspended accounts get no link; the administrator's second factor is not bypassed. Emails use the user's language.
- Invitation, access-request and password-reset pages send `Cache-Control: no-store` and `Referrer-Policy: same-origin`. The specification asks for `no-referrer`, but with it browsers send `Origin: null` on form POSTs and the CSRF check rejects every form; `same-origin` leaks nothing to other sites, and the token leaves the address bar right after the first open.
- Each audit event stores a server-generated request id (also returned as `X-Request-ID`, shown in the audit screen and the CSV). A value sent by the client is ignored.
- Known limits: response timing is not equalised between known and unknown addresses; existing sessions of the user are ended by the password change but the reset does not notify other devices.

### 7. Specification gaps: content (done)

- Move a folder to another folder or the room root, and a document to another folder of the same room. Moving across rooms is refused. Access follows the objects: a grant on a folder moves with it, and the folders it lands in start to apply. Cycles, name clashes and the depth limit are refused; each move is audited.
- Delete a document with all its versions (explicit confirmation, audited). Rows go in one transaction; files and cached previews are removed after it commits.
- Starter rooms (Investor overview, Due diligence, Restricted legal), created by an explicit administrator action, repeatable without duplicates, giving access to nobody. Names are neutral; instance-specific sets are not part of the repository.
- PDF parsing (upload check, page count, page images) runs in a separate Python process: clean environment without application secrets, 30 s wall-clock limit, on Linux 1 GiB address space, 25 s CPU time and 64 open files, output size cap. A failed or timed-out render gives `503` with `Retry-After` after the access check passed.
- Known limits: no restore of a deleted document; a backup taken at the moment a document is deleted can contain a row whose file is gone, so avoid deleting during a backup; on Windows only the time limit applies; page rendering is synchronous, so there is no separate "processing" state beyond the 503; moving does not notify people who lose access.

### 8. Watermarks and view analytics (done)

- Watermark: every preview page of a room with the switch on (default on) is stamped on the server with the visitor's email, UTC time, client address and a view code. The stamp is part of the image, so it cannot be removed in the browser. The page cache stays shared and unstamped; the stamp is added when the page is served, after the access check. The view code is also written to the audit event `document.viewed`, so a screenshot can be traced to an event. A code that is not logged for that user is ignored. If stamping fails the page is not shown.
- Analytics (administrator screen "View analytics", CSV export, audited): per document the number of visitors, views, time on pages and last view; per visitor which documents they can see but have not opened; views per day for 30 days; per page time for a PDF. Administrators are not counted.
- Time is measured by a small script (`static/js/viewer.js`) that reports the visible page numbers every 10 s. The server adds the elapsed time only for continuous viewing (pulses at least 5 s apart, gaps over 12 s add nothing) and caps one page at one hour.
- Known limits: a watermark deters and traces, it does not stop a photo of the screen; downloads for visitors with the download right stay original files without a stamp; the stamp uses Latin text only (the default font has no Cyrillic), so non-ASCII email characters show as `?`; time is approximate and can be inflated by a visitor who scripts the pulses; without JavaScript no time is recorded; "opened" comes from the audit log and counts a page open, not reading; the summary is computed on request and is meant for a few thousand events, not millions; events recorded before this stage have no view code.

### 9. Release readiness (done)

- CI (`.github/workflows/ci.yml`) runs on every push and pull request: the full test suite on SQLite and on PostgreSQL 16 (`TEST_DATABASE_URL`), `makemigrations --check`, the translation check, `ruff` for real errors (syntax, undefined names, unused imports), and a Docker job (`scripts/ci_compose_smoke.sh`).
- The Docker job builds the image, starts Compose with PostgreSQL, runs migrations, waits for the healthcheck, checks `/healthz/` and the closed sign-in page, makes a backup, deletes data, restores it with `restore_instance --replace` and checks the data is back, runs the PDF worker (Linux limits) and checks the container log for the secret key, the database password and a test password. All values are generated on the fly.
- PostgreSQL-only tests (`tests/test_postgres.py`): backup and restore round trip through `pg_dump` and `pg_restore`, refusal to restore over data, case-insensitive unique names.
- Found and fixed by CI: the `Dockerfile` did not build (a broken multi-line `CMD`); the image carried the PostgreSQL 17 client, so `pg_restore` failed against the PostgreSQL 16 server (the image now installs `postgresql-client-16`).
- Known limits: CI does not check real SMTP, native-speaker translation quality, phones, the Nginx and Caddy examples, or the published ports of a real host; the workflow actions still run on a deprecated Node 20 runtime and need a version bump later; the smoke test creates data through the shell, not through the browser; no dependency pinning or vulnerability scan yet.

### 10. Questions and answers (done)

- A visitor asks a question about a room, a folder or a document, only about what the access policy already shows them (and only after accepting the room's NDA). Administrators answer rather than ask. At most 10 questions per visitor per hour.
- Visibility: a question is private to its author. The administrator can share an answered question with other visitors who can see that area (a document follows its current folder, so moving it changes the audience); the author's name is hidden from them. If access is revoked, the author no longer sees the question either.
- The administrator has an inbox with filters (status, assignee, overdue), assigns a responsible administrator and a due date, answers with text and one PDF, DOCX or XLSX attachment (same checks as documents, stored in the private volume under `qa/`, served only through the visibility rules), closes and reopens.
- Emails (after the transaction commits, in the recipient's language): administrators on a new question, the author on the first answer. They contain only a link, never the question or the answer text; the audit log stores only codes and ids.
- The inbox exports to XLSX; text is written as text cells, so a value starting with `=` is not a formula. Backups and restores include the attachments.
- Known limits: one question has one answer (follow-ups are new questions); no search, no editing of a sent question, a single attachment per answer, an attachment is a plain download without a watermark and is not scanned for malware; a later update of an answer sends no new email; the person who asked sees the question and answer even after the question is closed, but not after their access ends; sharing shows the question text to others, which the asking form says.

### 11. Release candidate (done)

- Reviewed in a browser: the administrator screens (inbox, answer, analytics, audit, access, room settings, instance settings, invitations, requests) on desktop and at 375 px; no horizontal scrolling on any of 16 screens (wide tables scroll inside their card). The administrator header now puts the many links on their own row. A saved due date was not shown in the Russian, Ukrainian and German interface (the date input needs `yyyy-mm-dd`); fixed for all date fields.
- Security review of the whole code base against the rules in `CLAUDE.md` (an independent read-only pass plus the CI checks). No access-policy bypass was found. Fixed: deleting a folder could turn a shared answer into a room-wide one (now the question moves to the parent folder and stops being shared); numeric parameters such as `²` or a 5000-digit page number caused a server error (now `404`); two simultaneous first answers could send two emails; uncached page renders are limited to 120 per user per minute (`429`, so one visitor cannot occupy every worker).
- Added: a Content-Security-Policy (scripts only from the site, no framing, no plugins, forms only to the site), a session lifetime (12 hours from sign-in, `SESSION_COOKIE_AGE_SECONDS`), exact dependency versions in `requirements-lock.txt` (installed by the Docker image and audited by `pip-audit` in CI), Dependabot, newer GitHub Actions, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, the version in `dataroom/__init__.py`.
- Known limits (accepted, not fixed): the login and code-attempt limits check and record in two steps, so a burst of parallel guesses can slightly exceed them; five failed sign-ins for one address block that address for ten minutes, including an administrator's (the lockout is per address by design); the PDF worker has a clean environment and resource limits but no file-system, network or user isolation, so a PDF parser exploit could read other rooms' files (run the container without extra privileges, consider seccomp or a separate user); the retry of a failed decision email is not locked, so a rare double email is possible (rights are never granted twice); `seed_demo` has a fixed password and is safe only because it refuses to run outside `DJANGO_DEBUG=1`; sessions have a fixed lifetime but no idle timeout. No independent audit has been done.

### 12. Groups and the permission matrix (done)

- Groups of visitors inside a room: create, rename, delete, add and remove people. Access can be granted to a group exactly like to a person (a grant belongs to one person or one group). A person's rights are the union of their own grants and the grants of all their groups; there are no deny rules, so closed by default is unchanged. Revoking or expiring a group grant, removing a person from the group, deleting the group, suspending the person: each takes effect on the next request, because everything is computed in `apps/rooms/policy.py`.
- Access matrix: rows are the room and its folders, columns are groups and people with personal access. Each cell shows the direct level (no access, view only, can download) and a note for access inherited from a parent folder. Saving changes only the cells you changed; an old grant is revoked and a new one created, so the audit log shows every change. The expiry of a replaced grant is kept.
- "View as": a read-only page that shows the folders and documents a person can see and whether they can download, computed by the same policy. Documents are not opened in the person's name, nothing is recorded as their activity, the administrator's session does not change. Opening it is written to the audit log.
- An invitation can name a group; the person joins it when the invitation is accepted (once, however many times the link is used).
- Audit events: `group.created`, `group.renamed`, `group.deleted`, `group.member_added`, `group.member_removed`, `access.view_as_opened`. `seed_demo` creates two groups with different rights.
- Known limits: grants are on rooms and folders (a document follows its folder), so the matrix has no per-document rows; groups are flat (no nested groups) and belong to one room; "View as" shows the structure and rights, not the rendered pages, and does not show what a person has answered or asked; a group member sees only what the group's grants allow, plus their personal grants; adding a person to a group does not send an email.

## After the first release

Ordered roughly by expected value; none of these block the first release.

| Feature | Notes |
|---|---|
| Questions: next steps | Threaded follow-ups, search, edit before answer, daily digests, several attachments |
| Groups: next steps | Nested groups, groups across rooms, per-document rights, invitations to many people at once |
| Stamped downloads | The same stamp inside downloadable PDF files |
| Deeper analytics | Cohorts, charts, per-visitor reports, scheduled email digests |
| Tamper-evident audit log | Hash chain over log entries, periodic anchoring |
| Search and OCR | Full-text search inside documents, OCR for scans in all supported languages |
| Single sign-on | OIDC and SAML, SCIM provisioning |
| Electronic signing of NDAs | Today an NDA is accepted with a recorded version |
| Redaction | Permanent blackout of areas into a new file |
| Integrations | Webhooks, API keys, import from cloud storage |
| More languages | Add a locale catalogue and enable it in settings |

## Not planned

- A shared multi-tenant SaaS for many projects in one deployment.
- Guarantees against screen capture; deterrents (watermarks, legal terms) only.

## Contributing translations

Source strings are English. Catalogues live in `locale/<code>/LC_MESSAGES/django.po`. Native-speaker review of Russian, Ukrainian and German is especially welcome.
