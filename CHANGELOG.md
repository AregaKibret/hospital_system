# Changelog

All notable changes to this project are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/), and the project
intends to follow [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) going
forward — no version has been formally tagged in git yet (see
[How to Create a GitHub Release](docs/DEPLOYMENT.md#tagging-a-release)), so this file
reconstructs the build history from the implementation itself rather than from commit
messages, which have not tracked feature granularity to date.

## [Unreleased]

Nothing currently pending.

---

## [1.0.0] — 2026-07-21 — First Production Release

### Added — Hospital Configuration & Versioning
- **HospitalProfile** model: hospital name, logo, stamp, address, phone, email, website,
  license number, TIN, currency, timezone, and document header/footer text.
- **SystemModule** model: enable/disable functional modules (21 pre-seeded modules across
  Clinical, Diagnostic, Operations, Administrative, Financial, and HR categories).
  Core modules (registration, billing, consultation, reports, audit) cannot be disabled.
- **SystemVersion** model: full release history with version number, build, release date,
  release notes, DB schema version, and API version. `is_current` flag auto-maintained.
- **LicenseInfo** model: license key, type, max users, max branches, expiry, and support dates.
- **Context processor** (`hospital_context`): injects `hospital`, `enabled_modules`,
  `all_modules`, and `system_version` into every template automatically.
- **Hospital Profile settings page** (`/config/hospital/`): full form for all profile fields
  including logo and stamp file upload. Administrator only.
- **Module Management page** (`/config/modules/`): toggle switches grouped by category.
  Core modules shown as locked. Administrator only.
- **About / System Info page** (`/config/about/`): version card, hospital info, license status,
  and tech stack. Accessible to all authenticated users.
- **Version History page** (`/config/versions/`): all releases in reverse date order.
- **License Info page** (`/config/license/`): license details and status.
- **`setup_hospital_config` management command**: seeds HospitalProfile, all 21 SystemModules,
  and SystemVersion v1.0.0. Use `--force` to re-seed.
- **`includes/print_header.html`**: reusable printable document header include with logo, name,
  address, phone, email, license number, TIN, and stamp — for use in all print templates.
- **`base.html` updated**: nav bar now shows hospital logo (if set) and hospital name/tagline
  instead of hardcoded "Hospital Management". Footer shows hospital name and system version.
- **User menu**: Hospital Profile, Module Management, and About/Version links added for admins.
- **`VERSION` file**: single-source version string (`1.0.0`) at project root.
- **`manage_system_config` permission**: new permission for hospital profile and module admin.

### Added — Surgery Workflow (from previous sprint)
- **Surgery pre-deposit workflow**: collect actual surgery deposit (or approve credit) when
  patient arrives, before surgery. `SurgeryPreDeposit` model with `surgery_pre_deposit`,
  `surgery_pre_deposit_receipt`, `surgery_deposit_settlement`, and receipt URLs.
- **Surgery orders auto-route to reception**: new surgery orders created at
  `AWAITING_DECISION` status, skipping the manual "Send to Reception" step.
- **Settle Account & Discharge**: post-surgery discharge now routes through deposit
  settlement when a pre-deposit exists. Settlement receipt printed after discharge.

### Added — Reports (from previous sprint)
- **Doctor Activity & Income Report** (`/reports/clinical/doctor-activity/`): revenue per
  doctor broken down by service type (Consultation, Lab, Imaging, Medication, Procedure,
  Surgery, Bed/Room, Nursing, Other). Doctor and department dropdown filters. Excel/CSV export.
- **Surgeon Performance Report** fixed: surgeon dropdown added; `in_progress` and `pending`
  annotations corrected; surgeon name display fixed.
- **Doctor Performance Report** fixed: replaced free-text search with doctor dropdown;
  date filter moved into annotations to show all active doctors correctly.

### Added — Clinical (from previous sprint)
- **Physical Examination embedded in clinical note form**: physical exam fields appear above
  Assessment in the H&P note form and save as a linked `PhysicalExamination` record.

---

## [0.1.0] — Initial feature-complete baseline

This is the first version with the full breadth of modules described in `README.md`. Grouped
by area, most recently added first:

### Added — Documentation
- `README.md`, `CHANGELOG.md`, `requirements-dev.txt`, expanded `.env.example`, and the full
  `docs/` guide set (installation, configuration, database, API, developer, deployment,
  backup/recovery, security, testing, maintenance, troubleshooting).
- Administrator User Manual and full workflow reference (delivered as PDF).

### Added — Patient Attachment & Document Management
- `AttachmentCategory` (27 pre-seeded, admin-configurable, per-role upload restriction via
  `restricted_to_roles`), `PatientAttachment` (versioned, soft-deletable, confidentiality flag,
  optional links to Visit/Admission/Surgery/LabOrder/ImagingOrder/ProcedureOrder),
  `AttachmentComment`.
- Upload via file picker, drag-and-drop, or webcam capture; preview/download via authenticated
  streaming (no public media URL); 7 reports; new `Medical Records Officer` role.

### Added — Inventory Item Master Recreation
- `InventoryItem` extended (item code, generic name, item group, dosage form/strength,
  therapeutic category, 3-tier packaging units, multi-tier pricing, tax type); new `ItemGroup`
  and `UnitOfMeasure` models.
- `import_inventory_master` management command — idempotent import of the hospital's real
  ~3,845-item legacy catalog.
- New Item Master List report; item list/search extended.

### Added — Inpatient Admission & Bed Management
- `AdmissionRequest` and `AdmissionDepositRule` models; formal request → optional review →
  deposit gate → bed assignment pipeline, coexisting with the pre-existing one-step Quick Admit.
- Discharge gating on `Admission` (balance settled + doctor approval required to release a bed).
- Admission dashboard and 8 new reports (register, daily, length-of-stay, deposit collection,
  revenue, department stats, readmissions, cancellations).

### Added — Dedicated Nursing Module
- 8 structured assessment models (Initial/Daily, Pain, Fall Risk, Pressure Ulcer, Nutritional,
  Fluid Balance, Glasgow Coma Scale, Care Plan) plus shift handover notes.
- Generalized the department-store/transfer-request stack to carry medications *or*
  `InventoryItem` consumables through one pipeline; new `Ward Supervisor` role and optional
  pre-approval stage.
- Ward dashboard, ward-scoped patient list, MAR integration, billable consumable usage.

### Added — Laboratory Result Entry
- 12 result-input types, `LabResultEntry` (one row per analyte per order), reference-range and
  critical-value interpretation (`core/lab_results.py`), printable per-order result report.

### Added — Laboratory Test Master Recreation
- `LabCategory`, `LabTestGroup`, `LabTestResultOption`, `LabReferenceRange`; `LabService`
  extended with category/group/panel/reference-range/QC fields.
- `import_lab_catalog` management command — idempotent import of the hospital's real ~390-test
  legacy catalog (category → panel → test tree).
- 5 new lab reports.

### Added — Employee Signature Management
- `EmployeeSignature` model (soft-deactivate history, not hard delete), authenticated image
  serving (`MEDIA_URL` is never publicly wired), and automatic insertion into prescriptions,
  lab reports, and perioperative documents.

### Added — Facility & Hospital Location Management
- `Building` → `Floor` → `Ward` → `Room` → `Bed` hierarchy; `Admission` model with quick-admit,
  transfer, and discharge flows; facility dashboard and reports.

### Added — Physical Inventory Count & Opening Balance
- `InventoryPeriod`, `PhysicalCount`/`PhysicalCountLine` (spanning General Store / Medication /
  Department Store domains), `InventoryAdjustment`, `InventoryPeriodBalance` with automatic
  closing-to-opening carry-forward.

### Added — Card & Consultation Type Configuration
- `CardType`, `ConsultationType`, `CardSettings` — admin-configurable fees/validity replacing
  any hardcoded pricing at registration and check-in.

### Added — Follow-Up & Walk-In Appointments
- Doctor-initiated follow-up scheduling; receptionist walk-in registration directly into the
  queue.

### Added — Foundational Modules (pre-existing baseline)
- Patient registration, appointments & doctor scheduling, queue/triage, doctor consultation &
  EMR (clinical notes, vitals, diagnosis), laboratory & radiology ordering, pharmacy
  (prescription workflow + POS), surgery/OR/anesthesia & perioperative documentation, general
  store & medication inventory, department pharmacy, billing/invoicing/payments, HR, reports,
  and the RBAC/audit-log foundation every later module builds on.

## Upgrade Instructions

There have been no breaking changes to date — every migration in this project has been
additive (new fields/models with safe defaults, no destructive `AlterField`/`RemoveField`
operations on existing data). Standard upgrade path:

```bash
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py setup_rbac   # syncs any newly-added permissions/roles
python manage.py collectstatic --noinput
```

## Breaking Changes

None recorded to date.
