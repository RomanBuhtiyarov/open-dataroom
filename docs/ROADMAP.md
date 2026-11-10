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
| 6 | Specification gaps: security | done | Password reset by one-time link, no-store and no-referrer on token pages, request id in the audit log |
| 7 | Specification gaps: content | done | Move folders and documents, delete documents, starter rooms, PDF parsing in a separate process |

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
- Invitation, access-request and password-reset pages send `Cache-Control: no-store` and `Referrer-Policy: no-referrer`; other pages keep `same-origin`.
- Each audit event stores a server-generated request id (also returned as `X-Request-ID`, shown in the audit screen and the CSV). A value sent by the client is ignored.
- Known limits: response timing is not equalised between known and unknown addresses; existing sessions of the user are ended by the password change but the reset does not notify other devices.

### 7. Specification gaps: content (done)

- Move a folder to another folder or the room root, and a document to another folder of the same room. Moving across rooms is refused. Access follows the objects: a grant on a folder moves with it, and the folders it lands in start to apply. Cycles, name clashes and the depth limit are refused; each move is audited.
- Delete a document with all its versions (explicit confirmation, audited). Rows go in one transaction; files and cached previews are removed after it commits.
- Starter rooms (Investor overview, Due diligence, Restricted legal), created by an explicit administrator action, repeatable without duplicates, giving access to nobody. Names are neutral; instance-specific sets are not part of the repository.
- PDF parsing (upload check, page count, page images) runs in a separate Python process: clean environment without application secrets, 30 s wall-clock limit, on Linux 1 GiB address space, 25 s CPU time and 64 open files, output size cap. A failed or timed-out render gives `503` with `Retry-After` after the access check passed.
- Known limits: no restore of a deleted document; a backup taken at the moment a document is deleted can contain a row whose file is gone, so avoid deleting during a backup; on Windows only the time limit applies; page rendering is synchronous, so there is no separate "processing" state beyond the 503; moving does not notify people who lose access.

## After the first release

Ordered roughly by expected value; none of these block the first release.

| Feature | Notes |
|---|---|
| Questions and answers | Investor questions on documents and folders, owners, deadlines, answers with attachments, XLSX export |
| Groups and permission matrix | Grant access to groups instead of individuals, inheritance, "view as" |
| Dynamic watermarks | Email, IP and time on preview pages |
| View analytics | Time per page, unopened documents, activity per day |
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
