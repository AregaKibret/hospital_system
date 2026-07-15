# Troubleshooting Guide (Developer / Ops)

Operational and installation-level issues. For end-user workflow issues (e.g. "why doesn't this
doctor show up in a dropdown"), see the **Administrator User Manual**'s Troubleshooting section
instead — this guide covers getting the application itself running and configured correctly.

## Installation Failures

- **`pip install` fails on `psycopg2-binary`** — usually a missing PostgreSQL client
  development headers issue on Linux/macOS. Install `libpq-dev` (Ubuntu:
  `sudo apt install libpq-dev`) or `postgresql` (macOS Homebrew provides the needed headers)
  before retrying, or use a Python version with a prebuilt wheel available.
- **`ModuleNotFoundError` after activating the virtual environment** — confirm you actually
  activated it (`venv\Scripts\activate` on Windows, `source venv/bin/activate` on Linux/macOS)
  before running `pip install -r requirements.txt`; a very common mistake is installing into
  the system Python instead.

## Migration Errors

- **`django.db.utils.ProgrammingError: relation "..." does not exist`** — migrations haven't
  been applied yet. Run `python manage.py migrate`.
- **`InconsistentMigrationHistory` or a migration dependency conflict** — usually means
  migrations were applied out of order against a database that already had a later migration's
  effects manually applied. Do not `--fake` your way past this without understanding why first
  — check `python manage.py showmigrations core` to see exactly which migrations the database
  believes are applied.
- **A new migration you generated looks like it's about to alter/remove an existing column with
  data in it** — stop and re-read `docs/DATABASE.md`'s Migration Strategy section before running
  it; split it into `RemoveField` + `AddField` + `RunPython` backfill instead of a destructive
  `AlterField`, per this project's established convention.

## Database Connection Issues

- **`could not connect to server: Connection refused`** — PostgreSQL isn't running, or
  `DB_HOST`/`DB_PORT` in `.env` don't match where it's actually listening. Confirm with
  `pg_isready -h <host> -p <port>`.
- **`FATAL: password authentication failed`** — `DB_USER`/`DB_PASSWORD` in `.env` don't match
  what you created in PostgreSQL (see `docs/INSTALLATION.md`'s database-creation step for each
  OS).
- **Works locally but fails after deploying** — confirm `DATABASE_URL` is actually set in the
  target environment; recall that `settings.py` checks `DATABASE_URL` **first** and only falls
  back to the individual `DB_*` variables if it's absent (`docs/CONFIGURATION.md`) — a stray
  `DATABASE_URL` pointing at the wrong database will silently override your `DB_*` settings.

## Missing Dependencies

`ImportError`/`ModuleNotFoundError` when running `manage.py` almost always means
`pip install -r requirements.txt` wasn't run inside the active virtual environment, or a new
dependency was added to `requirements.txt` by a teammate and you haven't re-run `pip install`
since pulling.

## Static Files Not Loading

- **In development** (`DEBUG=True`, `runserver`): Django serves static files automatically — if
  they're still not loading, check the browser console for a 404 and confirm the referenced
  path matches what's actually under `core/static/` (or wherever the specific asset lives).
- **In production**: static files are served by WhiteNoise from `STATIC_ROOT`
  (`staticfiles/`), which is only populated by `python manage.py collectstatic`. If static files
  are 404ing in production, this step was skipped — `build.sh` runs it automatically on Render,
  but if you're running this outside that pipeline, run it manually before starting Gunicorn.

## Media / Upload Issues

- **"File not found" when previewing/downloading an attachment or signature** — confirm
  `media/` actually exists and is writable by the application process, and specifically confirm
  it wasn't wiped by a redeploy (see the persistent-disk caution in `docs/DEPLOYMENT.md` — this
  is the single most common cause on Render's non-persistent-disk plans).
- **Upload rejected with a file-type or size error** — intentional validation, not a bug; check
  `ATTACHMENT_ALLOWED_EXTENSIONS`/`ATTACHMENT_MAX_BYTES` (or the signature equivalents) in
  `core/models.py` if the limits genuinely need to change for your deployment.
- **A previously-working attachment/signature link now 404s** — remember `MEDIA_URL` is never
  publicly served (`docs/CONFIGURATION.md`); the file must be fetched through
  `patient_attachment_preview`/`download` or `employee_signature_image`, never a direct
  `/media/...` URL. If something is linking directly to a `/media/...` path, that's the bug to
  fix, not the file storage.

## Email Configuration Problems

There is no email configuration to troubleshoot — no `EMAIL_BACKEND` is configured anywhere in
this project (`docs/CONFIGURATION.md`). If a workflow appears to be "supposed to" send an email
and isn't, the correct fix is recognizing no email-sending code exists for it, not debugging a
misconfigured SMTP setting.

## Permission Issues ("Access Denied" pages)

- Confirm the logged-in user's Role actually has the permission the view requires — check
  `core/permissions.py`'s `ROLE_PERMISSIONS` for that role, or use the Role permission-editor UI
  (`/settings/roles/<id>/edit/`) to see the exact checkbox state.
- After changing a Role's permissions, **existing logged-in sessions may not see the change
  immediately** — Django caches the permission set on the request's `User` object per-request
  from the database, so a change should actually apply on the user's very next request; if it
  doesn't appear to, have them log out and back in to rule out any stale client-side state.
- Remember `@hms_permission_required` requires **every** listed permission (AND, not OR) — a
  view gated by two permissions will deny a user who has only one of them.

## Session Problems

- **Users getting logged out unexpectedly** — check `core/middleware.py`'s
  `SESSION_TIMEOUT_MINUTES` (fixed at 120) before assuming it's a bug; an idle session past that
  threshold is force-expired by design.
- **A specific user needs to be force-logged-out right now** (e.g. suspected compromised
  session) — use the Session Dashboard (`/sessions/`, `core.manage_users`), not a database edit.

## Report Generation Failures

- **A report page throws an error with a specific date range** — check whether the underlying
  queryset assumes a non-null related field that's actually null for some historical rows (a
  common cause across this codebase, since several relationships are `SET_NULL` — see
  `docs/DATABASE.md`).
- **Excel export (`?export=excel`) fails** — `openpyxl` is the only Excel dependency; confirm
  it's installed (`pip show openpyxl`) and check the specific report's header/row-building code
  for a type mismatch (e.g. passing a `Decimal` where `openpyxl` expects a plain type can
  sometimes need an explicit cast, depending on the report).

## PDF Export Issues

There is no server-side PDF generation library in `requirements.txt` — every "PDF" a user gets
from this application is the browser's own Print-to-PDF applied to a print-styled HTML page
(see `docs/API.md`/`docs/CONFIGURATION.md` for the same point made about reports). If a report
prints blank or malformed:
- Confirm the report's queryset actually returned rows for the selected filters (an empty
  result set can render as a misleadingly blank print preview in some browsers).
- Confirm the page's print stylesheet (`.no-print` classes hiding filter controls, per the
  convention used across every report template) is present and not being stripped by a browser
  print-preview extension.

If your team specifically needs a server-generated PDF file (not browser print-to-PDF) for some
new deliverable, install a PDF library such as `reportlab` — this was done ad hoc for two
one-off documentation PDFs during this project's development, but `reportlab` was **not** added
to `requirements.txt` as an application dependency; add it explicitly and deliberately if a
real, ongoing in-app PDF feature is being built.
