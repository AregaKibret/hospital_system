# Deployment Guide

## What This Project Actually Supports Today

The project is configured for deployment to **Render.com** (`render.yaml` + `build.sh`),
running **Gunicorn** behind **WhiteNoise** for static files. There is **no Docker/
docker-compose/Dockerfile, no Nginx configuration, and no Kubernetes manifest anywhere in this
repository** — if your infrastructure needs containerization, that is new work to add (see
[Adding Docker Support](#adding-docker-support-not-currently-implemented) below), not something
to configure from existing files.

There is also no distinct "Testing" or "Staging" environment configuration checked in — only
"local development" (`DEBUG=True`, local Postgres) and "production" (`DEBUG=False`, the
Render service). Setting up a staging environment means provisioning a second Render service
(or equivalent) pointed at a second database, with its own `.env` — the application code
requires no changes to support this.

## Render.com (the supported path)

`render.yaml`:
```yaml
services:
  - type: web
    name: hospital-hms
    runtime: python
    buildCommand: "./build.sh"
    startCommand: "gunicorn hospital_system.wsgi:application"
    envVars:
      - key: SECRET_KEY
        generateValue: true      # Render generates and stores this for you
      - key: DEBUG
        value: "False"
      - key: PYTHON_VERSION
        value: "3.12.0"
      - key: DATABASE_URL
        fromDatabase:
          name: hospital-db
          property: connectionString

databases:
  - name: hospital-db
    databaseName: hospital_db
    user: hospital_user
    plan: free
```

`build.sh` (runs automatically on every deploy):
```bash
#!/usr/bin/env bash
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
python manage.py setup_rbac
```

**Steps to deploy:**
1. Push this repository to GitHub (already done — `github.com/AregaKibret/hospital_system`).
2. In the Render dashboard, "New +" → "Blueprint" → point it at the repo → Render reads
   `render.yaml` and provisions the web service + managed Postgres database automatically.
3. Render runs `build.sh` on every push to the connected branch — migrations and RBAC sync
   happen automatically, with no manual SSH step required.
4. `ALLOWED_HOSTS` is satisfied automatically: `settings.py` appends
   `RENDER_EXTERNAL_HOSTNAME` (a variable Render injects itself) to the configured list.

**Caution:** `build.sh` runs `setup_rbac` on every deploy, in production, unconditionally —
this (re)creates the same ~35 predictable demo accounts (password `Test@1234`) on every
production instance too. See `docs/SECURITY.md` for the recommended mitigation before a real
go-live.

## Domain Configuration & HTTPS/SSL

Render provides HTTPS termination and a `*.onrender.com` subdomain automatically; attaching a
custom domain and its TLS certificate is configured entirely in the Render dashboard (Settings
→ Custom Domains) — no application code or environment variable changes are needed. There is no
in-application HTTPS-redirect setting configured in `settings.py` (no `SECURE_SSL_REDIRECT`) —
Render's edge handles TLS termination in front of the app.

## Static & Media Files in Production

- **Static files**: `python manage.py collectstatic` (run automatically by `build.sh`) gathers
  everything into `staticfiles/`, served directly by the WhiteNoise middleware inside the same
  Gunicorn process — no separate Nginx/CDN static-file step is required or configured.
- **Media files** (`media/` — employee signatures, patient attachments): stored on **local
  disk** on the Render instance. Render's free/standard web services do **not** guarantee a
  persistent disk across deploys/restarts by default — confirm your Render plan has a persistent
  disk attached (or add cloud object storage, see below) before relying on uploaded files
  surviving a redeploy. This is a real operational gap worth resolving before production use of
  the Attachment/Signature modules at scale.

## Database Migration During Deployment

Handled automatically by `build.sh` (`python manage.py migrate` runs on every deploy, before the
new code starts serving traffic). Because migration history is additive-only (see
`docs/DATABASE.md`), this has been safe to run unattended to date — but **always back up the
database before a deploy that includes a non-trivial migration** (see
`docs/BACKUP_RECOVERY.md`), since an automatic migration step has no built-in rollback.

## Backup Before Deployment

Not automated — see `docs/BACKUP_RECOVERY.md` for the manual `pg_dump` procedure. Run it
yourself before any deploy you're not fully confident in, especially one containing a schema
migration.

## Reverse Proxy

Render's own edge network acts as the reverse proxy in front of Gunicorn — there is no
project-owned Nginx/HAProxy configuration to maintain.

---

## Adding Docker Support (Not Currently Implemented)

If you need to run this outside Render (a bare VM, on-prem, or another container platform), a
minimal `Dockerfile` would look like:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python manage.py collectstatic --noinput
CMD ["gunicorn", "hospital_system.wsgi:application", "--bind", "0.0.0.0:8000"]
```

paired with a `docker-compose.yml` running this alongside a `postgres:18` service and a
persistent volume mounted at `/app/media`. **This is a suggested starting point, not something
that exists in the repository today** — add and test it as its own piece of work before relying
on it, particularly the `media/` volume-persistence and `DATABASE_URL` wiring between the two
containers.

## Tagging a Release

No git tag exists in this repository yet. To cut one:

```bash
git tag -a v0.1.0 -m "Initial feature-complete baseline"
git push origin v0.1.0
```

Then turn it into a full GitHub Release (with notes, drawn from `CHANGELOG.md`) via the GitHub
web UI: your repo → **Releases** → **"Draft a new release"** → select the `v0.1.0` tag →
publish. (The `gh` CLI can do this in one step with `gh release create`, but it isn't installed
in this project's development environment — see `docs/MAINTENANCE.md` if you want to add it.)
