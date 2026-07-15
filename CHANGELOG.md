# Changelog

All notable changes to this project are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/), and the project
intends to follow [Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) going
forward — no version has been formally tagged in git yet (see
[How to Create a GitHub Release](docs/DEPLOYMENT.md#tagging-a-release)), so this file
reconstructs the build history from the implementation itself rather than from commit
messages, which have not tracked feature granularity to date.

## [Unreleased]

Nothing currently pending beyond what's listed under `0.1.0` below.

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
