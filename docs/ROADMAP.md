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
| 2 | Rooms, documents and access policy | next | Rooms, folder tree, documents and versions, access grants, central access policy, room templates, private file storage |
| 3 | Invitations and registration | planned | Personal invitation by link or QR code, email verification by one-time code, self-registration with administrator approval, translated emails, rate limits |
| 4 | Screens, audit and previews | planned | Administrator and investor screens, audit log, raster page previews for view-only documents, download by permission, revocation takes effect immediately |
| 5 | Operations | planned | Second instance from the same image, backup and restore procedure, deployment guide with reverse proxy examples |

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

### 2. Rooms, documents and access policy

- Rooms and nested folders; an access scope on a folder includes its descendants.
- Documents with immutable versions; a new version is published only after the file is stored successfully.
- Access grants per user for a room or folders, with an optional download right and expiry; multiple grants combine.
- Optional NDA requirement per room, with versioned, per-language NDA texts and a recorded acceptance.
- PDF, DOCX and XLSX accepted after server-side checks of type, extension and size.
- Room templates that can be applied repeatedly without creating duplicates or resetting permissions.

### 3. Invitations and registration

- Invitation links with opaque tokens stored only as hashes; opening the link never consumes it, submitting the form does, in one transaction.
- Ownership of the invited address confirmed by a one-time code.
- Self-registration with an approval queue; each request is decided once.
- Emails are sent after the transaction commits, in the recipient's language, and can be retried safely.
- Rate limits on sign-in, email sending and code checks.

### 4. Screens, audit and previews

- Screens for sign-in, invitation, registration, request status, administration, rooms, files, active access, audit log and document viewing.
- Audit events with UTC time, actor, event code, target and outcome.
- View-only documents are shown as page images through the same access checks; originals are available only with the download right.
- Mobile-friendly flow for scanning an invitation QR code.

### 5. Operations

- Reverse proxy examples for Nginx and Caddy.
- Consistent backups of the database and documents together; documented, tested restore.
- Verified isolation between two instances run from one image.

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
