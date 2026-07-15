# Hospital Management System (HMS)

A comprehensive, server-rendered Django hospital management system covering the full patient
journey — registration, appointments, triage/queue, doctor consultation & EMR, nursing,
laboratory, radiology, pharmacy, surgery/OR & anesthesia, facility & bed management, inpatient
admission & deposits, billing/finance, general & medication inventory, patient document
attachments, HR & digital signatures, and system-wide reporting & audit — built for a single
hospital deployment with fully role-based access control.

> This README reflects the system as actually implemented, not an aspirational feature list.
> See [`docs/`](docs/) for the full documentation set and `CHANGELOG.md` for what's shipped.

---

## Table of Contents

- [Features](#features)
- [System Architecture Overview](#system-architecture-overview)
- [Technology Stack](#technology-stack)
- [Folder Structure](#folder-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Application](#running-the-application)
- [Default Login Accounts](#default-login-accounts)
- [API Documentation](#api-documentation)
- [Common Commands](#common-commands)
- [Troubleshooting](#troubleshooting)
- [FAQ](#frequently-asked-questions)
- [License](#license)
- [Contribution Guidelines](#contribution-guidelines)
- [Version History](#version-history)
- [Contact](#contact-information)

---

## Features

| Domain | What's included |
|---|---|
| **Patient Registration & Cards** | Patient demographics, admin-configurable Card Types and Consultation Types, card issue/renewal billing |
| **Appointments** | Booking, doctor availability/schedules & exceptions, walk-ins, follow-ups, check-in-to-visit billing pipeline |
| **Queue & Triage** | Department queues, emergency triage prioritization |
| **Patient Flow** | Cross-department live visibility into every visit's current stage |
| **Doctor Consultation / EMR** | Tabbed visit chart: notes, vitals, diagnosis, lab/imaging orders, medications, prescriptions, procedures, nursing, attachments |
| **Laboratory** | Configurable test master (categories/panels/reference ranges), multi-test ordering, structured per-analyte result entry with auto-interpretation, release workflow |
| **Radiology** | Configurable imaging service catalog, ordering, reporting, release |
| **Pharmacy** | Prescription workflow (order → review → dispense, FEFO batch stock, auto-generated MAR), separate POS for walk-in sales |
| **Nursing** | Ward dashboard, 8 structured clinical assessments (Morse/Braden/MUST/GCS/etc.), MAR administration, ward consumables with auto-billing, medication/consumable request pipeline |
| **Surgery / OR / Anesthesia** | Surgery ordering & approval, OR scheduling, anesthesia records, operative/post-operative notes with version history, consumable billing |
| **Facility & Bed Management** | Building → Floor → Ward → Room → Bed hierarchy, quick admit, transfer, discharge |
| **Inpatient Admission** | Formal admission-request pipeline, configurable deposit rules, deposit-gated bed assignment, discharge gating (balance settled + doctor approval) |
| **Billing & Finance** | Item-level invoice tracking, cash/credit/insurance payments, discounts, item-level refunds, cash sessions |
| **Inventory** | General Item Master (imported from a real ~3,845-item legacy catalog), categories/item groups/units of measure, purchase orders, physical counts with period opening/closing balances |
| **Medication Inventory & Dept. Pharmacy** | Separate clinical drug catalog with batches/expiry, department (ward/ER/ICU/OR) stock with a request-approval-fulfillment pipeline |
| **Patient Attachments** | Upload/drag-drop/webcam capture, 27 configurable categories with per-role upload restriction, confidentiality gating, versioning, soft delete |
| **HR & Digital Signatures** | Employee records, attendance, leave requests, per-employee signature upload auto-inserted into printed clinical documents |
| **Reports & Analytics** | ~100 reports across every module with date/department/category filters, CSV/Excel export |
| **RBAC & Audit** | 35+ predefined roles, per-role permission editing, and an immutable audit log covering every significant action system-wide |

## System Architecture Overview

This is a **monolithic, server-rendered Django application** — one Django project
(`hospital_system`), one app (`core`), no separate frontend build, no microservices, no
background task queue.

```
Browser (Django templates + Tailwind CDN + vanilla JS)
        │  HTTPS
        ▼
Gunicorn (WSGI) ── WhiteNoise (static files)
        │
        ▼
Django (core app: 32 views_*.py modules, ~50 migrations)
        │
        ▼
PostgreSQL (single database, no read replicas / caching layer)
```

- **Every page is server-rendered HTML** — there is no REST API layer, no SPA, no JSON-first
  architecture. A handful of endpoints return JSON for AJAX widgets only (see
  [API Documentation](#api-documentation)).
- **File storage is local disk** under `MEDIA_ROOT` (`media/`). `MEDIA_URL` is intentionally
  **not** wired to Django's static file server — every uploaded file (signatures, patient
  attachments) is served through an authenticated view, never a public static path.
- **No cache layer, no message broker, no background job runner** exist in this codebase today.
- Permissions are enforced per-view via a custom `@hms_permission_required(...)` decorator
  checking Django's built-in `auth` permission system (Groups = Roles).

## Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.12+ | Pinned via `PYTHON_VERSION` in `render.yaml` for deployment |
| Framework | Django 6.x | `Django>=6.0,<7.0` in `requirements.txt` |
| Database | PostgreSQL | Via `psycopg2-binary`; `dj-database-url` reads `DATABASE_URL` in cloud deployments |
| WSGI Server | Gunicorn | Production entrypoint |
| Static Files | WhiteNoise | `CompressedManifestStaticFilesStorage` — no separate CDN/Nginx static serving required |
| Config | python-decouple | `.env`-driven settings |
| Frontend | Django Templates + Tailwind CSS (CDN) + vanilla JS | No React/Vue, no npm build step, no bundler |
| Date picker | Flatpickr (CDN) | Only third-party JS widget in use |
| Excel Import/Export | openpyxl | Used for report exports and the two legacy-data import commands |
| PDF | Browser-native print-to-PDF | No server-side PDF rendering library is part of the application itself |

**Not present in this project** (called out explicitly because they're commonly assumed for
systems like this): Django REST Framework, Celery, Redis, Docker, Node.js/npm/React/TypeScript/
Vite, any SMS/email gateway integration, any automated test suite.

## Folder Structure

```
hospital_system/
├── hospital_system/         # Django project package
│   ├── settings.py          # All configuration (env-driven via python-decouple)
│   ├── urls.py               # Root URLconf (mounts core.urls + /admin/)
│   └── wsgi.py
├── core/                     # The single Django app — the entire application lives here
│   ├── models.py             # ~6,700 lines — every model in the system
│   ├── urls.py                # ~680 lines — every URL route
│   ├── permissions.py        # ROLE_NAMES, ROLE_PERMISSIONS, DASHBOARD_MODULES — RBAC source of truth
│   ├── decorators.py         # @hms_permission_required
│   ├── audit.py              # log_action() — the audit-log writer used everywhere
│   ├── forms.py               # Django ModelForms used by a subset of views
│   ├── views.py               # Auth, user management, core/shared views
│   ├── views_<module>.py     # 30 module-specific view files (doctor, lab, pharmacy, nursing,
│   │                          #   surgery, facility, admissions, inventory, attachments, hr, …)
│   ├── views_reports.py      # Every cross-module report
│   ├── templates/            # Django templates, one subfolder per module
│   ├── management/commands/  # setup_rbac, seed_data, import_lab_catalog,
│   │                          #   import_inventory_master, setup_*_data
│   └── migrations/           # ~50 migrations, additive-only history
├── media/                    # Uploaded files (signatures, patient attachments) — gitignored
├── staticfiles/               # collectstatic output — gitignored
├── docs/                      # Extended documentation (this README links out to it)
├── requirements.txt           # Runtime dependencies
├── requirements-dev.txt       # Development-only dependencies
├── build.sh                   # Render.com build script (install → collectstatic → migrate → setup_rbac)
├── render.yaml                 # Render.com service + database definition
└── manage.py
```

## Prerequisites

- **Python 3.12+**
- **PostgreSQL 14+** (developed and tested against PostgreSQL 18)
- **Git**
- A **virtual environment** tool (`venv`, built into Python)

There is no Node.js/npm requirement — the frontend is served directly from Django templates.

## Installation

Full step-by-step instructions for Windows, Linux, and macOS are in
**[`docs/INSTALLATION.md`](docs/INSTALLATION.md)**. Quick version:

```bash
git clone https://github.com/AregaKibret/hospital_system.git
cd hospital_system
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env — see Configuration below
python manage.py migrate
python manage.py setup_rbac     # creates roles + sample users (see Default Login Accounts)
python manage.py runserver
```

## Configuration

All configuration is environment-variable driven via `python-decouple`. See
**[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md)** for every variable's purpose, and
**[`.env.example`](.env.example)** for a ready-to-copy template.

## Running the Application

```bash
python manage.py runserver              # http://127.0.0.1:8000/
python manage.py runserver 0.0.0.0:8000 # to reach it from another device on your network
```

In production, the process is `gunicorn hospital_system.wsgi:application` behind WhiteNoise for
static files — see **[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)**.

## Default Login Accounts

`python manage.py setup_rbac` creates ~35 sample accounts (one per role) for local development
and demos, **all with the password `Test@1234`**. A few examples:

| Username | Role |
|---|---|
| `sysadmin` | Administrator |
| `sara.tadesse` | Receptionist |
| `bekele.haile` | Doctor |
| `tigist.alemu` | Nurse |
| `meseret.kebede` | Laboratory Staff |
| `abebe.mulatu` | Pharmacy Admin |
| `kedir.seid` | Store Manager |
| `helen.assefa` | Medical Records Officer |

See `core/management/commands/setup_rbac.py` for the full list.

> **Change or remove these before any real deployment.** They exist purely to make every role
> immediately testable in development.

## Screenshots

Not included in this repository — the project has no screenshot-capture tooling installed
(no Playwright/headless-browser dependency), and committing binary images into a fast-moving
codebase like this tends to go stale quickly. To generate your own:

1. Run the dev server (`python manage.py runserver`) and log in with any sample account above.
2. Use your browser's own screenshot tool, or install Playwright (`pip install playwright && playwright install chromium`)
   and script captures of the pages you want documented.
3. Save them under `docs/screenshots/` and reference them from this section — that keeps them
   version-controlled alongside the feature they illustrate.

## API Documentation

This system does not expose a formal REST/JSON API — see
**[`docs/API.md`](docs/API.md)** for the small set of real JSON AJAX endpoints that do exist
(notification polling, lab test search, doctor slot/availability lookups) and why there's no
OpenAPI/Swagger spec or Postman collection to generate.

## Common Commands

```bash
python manage.py runserver                 # start the dev server
python manage.py migrate                    # apply migrations
python manage.py makemigrations core        # create a new migration after model changes
python manage.py setup_rbac                 # (re)sync roles/permissions + seed sample users
python manage.py createsuperuser             # create a Django admin superuser
python manage.py collectstatic               # gather static files (required before production)
python manage.py shell                       # interactive Django shell
python manage.py dbshell                     # interactive psql shell
python manage.py import_lab_catalog --path <file.xlsx>       # (re)import the lab test master
python manage.py import_inventory_master --path <file.xlsx>  # (re)import the inventory item master
```

Full command reference: **[`docs/MAINTENANCE.md`](docs/MAINTENANCE.md)**.

## Troubleshooting

See **[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)** for installation failures, migration
errors, database connection issues, static/media file problems, and permission issues, with
real, code-verified causes.

## Frequently Asked Questions

**Does this system have a REST API or a mobile app?**
No. It's a traditional server-rendered Django app. See `docs/API.md`.

**Can I use MySQL or SQLite instead of PostgreSQL?**
The codebase targets PostgreSQL specifically (`psycopg2-binary`, PostgreSQL-only SQL in a couple
of migrations). SQLite works for quick local exploration but is not the supported/tested path.

**Is there multi-tenancy / multi-hospital support?**
No — this is built for a single hospital deployment. There's also no admin-configurable
hospital name/logo/branding setting; see `docs/CONFIGURATION.md`.

**How do I add a new role, department, lab test, or inventory item?**
See `docs/DEVELOPER_GUIDE.md` (code-level) or the **Administrator User Manual** (`docs/` or
your Desktop copy) for the UI-level walkthrough — both exist for this system.

**Is there a test suite?**
Not yet — `core/tests.py` is the default empty Django stub. See `docs/TESTING.md` for how to
start one.

## License

No `LICENSE` file is currently present in this repository. Until one is added, all rights are
reserved by the project owner by default — add a `LICENSE` file to establish explicit terms
before external distribution or contribution.

## Contribution Guidelines

There is no `CONTRIBUTING.md` or formal contribution process yet. In the meantime:

- Follow the conventions documented in `docs/DEVELOPER_GUIDE.md` (naming, permission wiring,
  migration workflow) — the codebase is highly consistent about these across every module.
- Run `python manage.py check` and `python manage.py makemigrations --check --dry-run` before
  committing model changes.
- Keep this README, `CHANGELOG.md`, and `docs/` synchronized when you add a module, dependency,
  or configuration option — they are meant to track the implementation, not lag behind it.

## Version History

See **[`CHANGELOG.md`](CHANGELOG.md)** for the full, module-by-module build history.

## Contact Information

Repository: [github.com/AregaKibret/hospital_system](https://github.com/AregaKibret/hospital_system)
No dedicated support channel or maintainer contact is documented yet — add one here (email,
issue tracker, or internal ticketing system) once established.
