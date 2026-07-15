# Configuration Guide

Every configuration option this application actually reads, where it's read from, and what it
controls. Anything commonly expected but **not** implemented is called out explicitly rather
than omitted — see the [Gaps](#gaps--commonly-expected-settings-that-dont-exist) section at the
end.

All configuration is environment-variable driven via `python-decouple`, read in
`hospital_system/settings.py`. Copy `.env.example` to `.env` and fill it in — see that file
for the same information in template form.

## Database Settings

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | No | — | If set, takes priority over every `DB_*` variable below. Format: `postgres://USER:PASSWORD@HOST:PORT/DBNAME`. This is how Render.com (and most managed Postgres providers) inject connection details. |
| `DB_NAME` | No | `hospital_db` | Used only when `DATABASE_URL` is unset. |
| `DB_USER` | No | `postgres` | ″ |
| `DB_PASSWORD` | No | `''` | ″ |
| `DB_HOST` | No | `localhost` | ″ |
| `DB_PORT` | No | `5432` | ″ |
| `DB_CONN_MAX_AGE` | No | `60` | Seconds a DB connection is reused before being closed and reopened. |

The engine is hardcoded to `django.db.backends.postgresql` — there is no code path for
MySQL or SQLite in `settings.py` (though Django's SQLite backend would technically work for
quick local exploration if you edited `DATABASES` yourself; it is not the supported path).

## Secret Keys

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | **Yes — no default, app will not start without it** | Django's cryptographic signing key: session cookies, CSRF tokens, password-reset tokens. Generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. Must be unique per environment and never committed to git. |

## Core Django Settings

| Variable | Default | Purpose |
|---|---|---|
| `DEBUG` | `False` | `True` enables verbose error pages and disables some production hardening. Never `True` in a real deployment. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames this instance will serve. On Render.com, `RENDER_EXTERNAL_HOSTNAME` is appended automatically at runtime (see `settings.py` lines 14-18) — no manual action needed there. |

## Time Zone & Localization

Both are **fixed in code**, not environment-configurable:

- `TIME_ZONE = 'Africa/Addis_Ababa'` (`hospital_system/settings.py`)
- `LANGUAGE_CODE = 'en-us'`

To change either for a different deployment, edit `settings.py` directly and redeploy — there
is no admin UI or env var for this (see the Gaps section).

## Static & Media Files

Also fixed in code, not environment variables:

| Setting | Value | Notes |
|---|---|---|
| `STATIC_URL` | `/static/` | |
| `STATIC_ROOT` | `<project root>/staticfiles/` | Populated by `python manage.py collectstatic` |
| `STATICFILES_STORAGE` | `whitenoise.storage.CompressedManifestStaticFilesStorage` | Static files are served directly by the Django process via WhiteNoise — no separate Nginx/CDN static-file step is required |
| `MEDIA_URL` | `/media/` | **Not wired to Django's static file server** — see below |
| `MEDIA_ROOT` | `<project root>/media/` | Uploaded files (employee signatures, patient attachments) |

> **Important:** `MEDIA_URL` is intentionally never connected to a public file-serving route
> (no `static()` helper for it in `hospital_system/urls.py`). Every uploaded file is served
> through an authenticated Django view instead (see `core/views_signatures.py` and
> `core/views_attachments.py`) so uploaded documents are never reachable at a guessable public
> URL. Do not "fix" this by adding `static(settings.MEDIA_URL, ...)` — that would remove the
> file-serving authentication these two modules depend on.

## Security Settings

Fixed in `settings.py`, not environment-configurable:

```python
X_FRAME_OPTIONS = 'SAMEORIGIN'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
```

Password validation uses Django's stock validators (minimum 8 characters, not too similar to
the username, not a common password, not all-numeric) — see `docs/SECURITY.md` for the full
security posture.

## Logging

No custom `LOGGING` configuration exists in `settings.py` — Django's default logging (errors to
console/stderr) applies. There is no `LOG_LEVEL` environment variable read anywhere in the
codebase.

## Backup Settings

None — there is no in-application backup configuration. See `docs/BACKUP_RECOVERY.md` for the
recommended external (`pg_dump`-based) procedure.

---

## Gaps — Commonly Expected Settings That Don't Exist

Documented explicitly so you don't spend time hunting for where these are configured — they
aren't, anywhere in the codebase:

- **Email settings** (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
  `EMAIL_BACKEND`) — no email sending capability exists at all.
- **SMS settings** — no SMS gateway integration exists.
- **Payment gateway configuration** — all payment recording is manual (a cashier enters an
  amount and method); there is no integration with an external payment processor/gateway.
- **`REDIS_URL` / `CELERY_BROKER_URL`** — no cache layer, no background task queue.
- **Cloud storage configuration** (`STORAGE_CONFIGURATION`, AWS/GCS/Azure variables) — file
  storage is local disk (`MEDIA_ROOT`) only.
- **Hospital identity settings** — no admin-configurable hospital name, logo, address, contact
  info, license info, currency, or date/time display format exists anywhere. The currency label
  `"ETB"` is hardcoded directly into view files rather than being a setting.
- **`LOG_LEVEL`** — not read anywhere; Django's default logging applies unconfigured.

If any of these become real requirements, they would need to be built as new features (a new
settings model + admin UI, or new `settings.py` entries + a real integration), not merely
"configured" — there is no dormant integration waiting to be switched on.
