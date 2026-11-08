# Open Data Room — project instructions

Self-hosted open-source data room. One project, one instance, one Docker image.

Read `docs/ROADMAP.md` for scope and milestones and `README.md` for commands. If `CLAUDE.local.md` exists, it holds private, instance-specific notes (not published).

## Stack

Python 3.12, Django 5.2 LTS, PostgreSQL 16, Django templates, minimal JavaScript. Compose: web, PostgreSQL, private volume.

## Rules

- Closed by default. Every access check (list, preview, download, Range, direct URL) goes through one access policy.
- Invitation tokens are stored only as hashes. Tokens, passwords, codes and file contents never go to application, proxy or audit logs.
- A GET on an invitation page never consumes the token; only the POST does, inside a transaction.
- Send emails after the transaction commits; a retry must not grant rights again.
- All UI, email and error strings go through gettext in `ru`, `uk`, `de`, `en`. No hard-coded text in templates or JS. The audit log stores event codes; labels are translated on display.
- Secrets come from the environment only. Never commit `.env`. Keep `.env.example` free of real values.
- Use synthetic data for demos and tests. Do not add real customer names, brands or documents to the repository.

## Before finishing a milestone

Run `pytest`, `makemigrations --check`, and keep the `.po` catalogues in sync. Report the actual commands, results and open limitations.
