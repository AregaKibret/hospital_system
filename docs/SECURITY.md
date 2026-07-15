# Security Guide

## Authentication

Standard Django session-cookie authentication (`django.contrib.auth`) — no OAuth, no SSO, no
API tokens, no multi-factor authentication. Login is at `/accounts/login/`
(`LOGIN_URL`/`LOGIN_REDIRECT_URL`/`LOGOUT_REDIRECT_URL` in `hospital_system/settings.py`).
Passwords are hashed using Django's default hasher (PBKDF2) — no custom `PASSWORD_HASHERS`
override exists.

## Authorization / Role-Based Access Control (RBAC)

Every gated view is decorated `@hms_permission_required('core.<codename>', ...)`
(`core/decorators.py`) — requires an authenticated session **and** every listed permission,
resolved via Django's standard `user.has_perm()` against the user's single assigned Group
("Role"). See `core/permissions.py` for the full role→permission map and
`docs/DEVELOPER_GUIDE.md` for how to extend it. There is no per-object/row-level permission
system beyond a small number of manually-coded exceptions (e.g. "an employee may always manage
their own signature, in addition to anyone holding the management permission" —
`views_signatures.py`'s `_can_manage()`).

## Password Policies

Django's stock `AUTH_PASSWORD_VALIDATORS` (`settings.py`): `UserAttributeSimilarityValidator`,
`MinimumLengthValidator` (default minimum 8), `CommonPasswordValidator`,
`NumericPasswordValidator`. There is **no password-expiry policy** and **no admin UI to change
these rules** — changing them requires editing `settings.py` directly.

## Session Management

- Idle-session timeout: a fixed **120-minute** constant (`SESSION_TIMEOUT_MINUTES` in
  `core/middleware.py`), enforced by `SessionActivityMiddleware` on every request (a handful of
  polling endpoints, e.g. `/notifications/unread-count/`, are explicitly excluded from resetting
  or being killed by this check — see `SessionActivityMiddleware.SKIP_PATHS`).
- `SESSION_COOKIE_HTTPONLY = True` — the session cookie is inaccessible to JavaScript.
- An Administrator can view all active sessions and force-logout any of them at
  `/sessions/` (`core.manage_users`) — see `core/views_session.py`.
- There is no configurable session-timeout setting exposed anywhere in the UI.

## CSRF Protection

Django's `CsrfViewMiddleware` is enabled project-wide (`MIDDLEWARE` in `settings.py`) and
`CSRF_COOKIE_HTTPONLY = True`. Every POST form in every template includes `{% csrf_token %}` —
this is the default Django behavior and has not been disabled anywhere (no `@csrf_exempt`
decorator exists anywhere in the codebase — confirmed by search).

## XSS Protection

Django's template engine auto-escapes all variable output by default, and this project relies
on that default almost everywhere — a search of every template found exactly **one** file using
the `|safe` filter (`core/templates/reports/hub.html`). Review that specific usage before
changing it, and treat any *new* use of `|safe`/`mark_safe` anywhere in the codebase as
something that needs a specific, deliberate security review (why the content is trusted, and
where it originates) before merging.

## SQL Injection Protection

Every database query in this project goes through the Django ORM (`Model.objects.filter(...)`,
etc.) — a full search of `core/*.py` for raw SQL execution (`cursor()`, `.raw()`) returns **zero
matches**. As long as future code continues to use the ORM (rather than string-formatted raw
SQL), this class of vulnerability is structurally avoided.

## File Upload Security

Every upload path in the system (Employee Signatures, Patient Attachments) follows the same
pattern:

- An **extension whitelist** checked against the uploaded filename (e.g.
  `ATTACHMENT_ALLOWED_EXTENSIONS` in `core/models.py`) — files outside the whitelist are
  rejected with a `ValidationError` before ever being saved.
- A **byte-size cap** (`ATTACHMENT_MAX_BYTES` = 25 MB, `SIGNATURE_MAX_BYTES` = 2 MB).
- The **original filename is discarded** — every stored file is renamed to a random
  `uuid.uuid4().hex` plus its extension, scoped into a per-owner folder
  (`attachments/patient_<id>/...`, `signatures/employee_<id>/...`). This avoids both filename
  collisions and path-traversal via a crafted filename.
- Files are **never served from a public static path** — `MEDIA_URL` is deliberately not wired
  to Django's static file server (see `docs/CONFIGURATION.md`); every download/preview goes
  through an authenticated, permission-checked view (`FileResponse` after an explicit
  `request.user.has_perm(...)` check), so an uploaded file's storage path being guessed doesn't
  expose it — the URL alone is not sufficient to retrieve the file.
- **Not implemented:** no virus/malware scanning of uploaded files exists.

## Audit Logging

Every significant action (create/update/delete/approve/reject/payment/refund/access-to-
confidential-data, and more) writes an immutable `AuditLog` row via `core.audit.log_action(...)`
— see `docs/DEVELOPER_GUIDE.md`'s "Audit Logging Convention" for the exact call pattern, and
`docs/DATABASE.md` for the indexes that make this queryable at scale. There is no code path that
deletes or edits an `AuditLog` row after creation.

## Encryption

- **In transit**: handled at the hosting-platform edge (Render terminates TLS in front of the
  application — see `docs/DEPLOYMENT.md`); there is no in-application HTTPS-redirect setting
  (`SECURE_SSL_REDIRECT`) configured, since that's handled upstream on the supported deployment
  path.
- **At rest**: no field-level encryption exists for any model — patient demographic and clinical
  data is stored in plain columns in PostgreSQL, relying on the database/disk-level security of
  wherever it's hosted. If field-level encryption of specific PII columns becomes a requirement,
  it would be new work (e.g. `django-cryptography` or an equivalent) rather than a setting to
  toggle on.
- **Passwords**: hashed (never stored in plaintext, never logged) via Django's default hasher.

## Known Gaps / Recommendations Before a Real Go-Live

These are genuine, verified gaps — not hypothetical hardening suggestions:

1. **Predictable demo accounts**: `python manage.py setup_rbac` creates ~35 accounts with the
   password `Test@1234`, and runs unconditionally in production too (it's part of `build.sh`).
   **Before any real deployment**, change or disable these accounts — the command itself has no
   `DEBUG`-gating to do this for you.
2. **No account lockout after repeated failed logins** — failed attempts are logged (and visible
   in the Security Report) but never automatically lock the account. If this is a compliance
   requirement, it needs to be built.
3. **No MFA** — single-factor (password only) authentication for every role, including
   Administrator.
4. **No malware scanning on uploads.**
5. **No field-level encryption for patient PII at rest.**
