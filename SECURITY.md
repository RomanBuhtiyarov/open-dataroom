# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private reporting:
**Security → Report a vulnerability** on the repository page
(<https://github.com/RomanBuhtiyarov/open-dataroom/security/advisories/new>).

Include the version or commit, what you did, what you expected and what happened. Use synthetic data in your
report; never attach real documents or credentials. You will get an answer as soon as the maintainer can read it;
please allow a few days.

## Supported versions

The project is in early development. Only the latest commit on `main` and the latest release are supported.

## What the project promises

These are design rules, checked by tests and by CI (see `CLAUDE.md` and `docs/ROADMAP.md`):

- Closed by default: every list, preview, download, range request and direct URL goes through one access policy.
- Invitation tokens are stored only as hashes. Tokens, passwords, codes and file contents are not written to
  application, proxy or audit logs.
- Opening an invitation or password-reset link does not consume it; only the confirming POST does.
- Emails are sent after the database transaction commits.
- Secrets come from the environment only.

## What it does not promise

- A watermark and a view-only mode deter and trace leaks; they cannot stop a photo of the screen.
- The audit log is not tamper-evident yet.
- Uploaded files are checked by type and structure, not scanned for malware.
- The project has had automated tests, CI and an internal review. It has **not** had an independent security audit.
  Run it behind HTTPS, keep backups encrypted and off the server, and read the known limits in `docs/ROADMAP.md`
  and `docs/DEPLOYMENT.md` before storing sensitive documents.
