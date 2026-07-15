# Developer Guide

How this codebase is organized, the conventions it follows consistently across every module,
and step-by-step instructions for extending it the same way the existing code was built.

## Project Architecture

One Django project (`hospital_system`), one Django app (`core`). There is no app-per-module
split — every model, view, and template lives inside `core`, organized by **file naming
convention** rather than separate Django apps:

- `core/models.py` — every model, in one file (~6,700 lines), grouped by module with `# ══...`
  section-header comments (search for `"PATIENT ATTACHMENT"` or similar to jump to a section).
- `core/views_<module>.py` — one file per functional module (32 total). A view file typically
  contains: a dashboard view, list/detail/create/edit views, action views (approve/reject/etc.),
  and sometimes its own report views, all gated by `@hms_permission_required`.
- `core/urls.py` — every route, in one file (~680 lines), grouped by module with comment headers
  matching the view files.
- `core/permissions.py` — the single source of truth for roles and what they can do (see
  [How to Add New Roles/Permissions](#how-to-add-a-new-role) below).
- `core/templates/<module>/` — one template subfolder per module, mirroring the view file split.

There is deliberately **no `serializers.py`, no `api/` app, no DRF** — see `docs/API.md`.

## Coding Standards & Naming Conventions

These are observed with near-total consistency across the whole codebase — follow them for any
new code:

- **View functions**: `snake_case`, named `<noun>_<verb>` (`patient_attachment_upload`,
  `admission_request_review`), not `<verb>_<noun>`.
- **Permission codenames**: `snake_case`, action-first (`manage_inventory`, `view_attachments`,
  `upload_attachment`), always referenced in code as the fully-qualified string
  `'core.<codename>'`.
- **Models**: `PascalCase`, singular (`PatientAttachment`, not `PatientAttachments`).
  `TextChoices`/`IntegerChoices` inner classes for any status/type field, never a bare
  `CharField` with a comment listing allowed values.
- **Templates**: `snake_case.html`, `_leading_underscore.html` for a partial/include
  (`_priority_badge.html`, `_status_badge.html`).
- **Auto-generated document numbers**: computed in the model's `save()` override as
  `f"{PREFIX}-{timezone.localdate():%Y%m}-{next_id:04d}"` (or similar) — see `Invoice.save()`
  for the canonical example. Keep this pattern for any new numbered document type rather than
  inventing a different format.
- **Soft delete over hard delete** for anything with legal/audit weight: an `is_deleted`
  boolean plus `deleted_by`/`deleted_at`/`delete_reason`, never an actual `DELETE` — see
  `PatientAttachment` for the reference implementation, including its `restore` action.
- **File uploads**: a module-level `<THING>_ALLOWED_EXTENSIONS` tuple and `<THING>_MAX_BYTES`
  constant, a `validate_<thing>_file(file)` function raising `ValidationError`, and an
  `<thing>_upload_path(instance, filename)` function that randomizes the stored filename via
  `uuid.uuid4().hex` — see `validate_attachment_file`/`patient_attachment_upload_path` in
  `core/models.py` for the template to copy.
- **Audit logging**: every mutating view calls `core.audit.log_action(user, action, module,
  description=..., request=request)` — see [Adding Audit Logging](#audit-logging-convention).

## Module Structure — What a "Module" Looks Like Here

Using the Attachment module as the reference example (the most recently built, and the cleanest
illustration of every convention at once):

```
core/models.py           → AttachmentCategory, PatientAttachment, AttachmentComment
core/forms.py             → (not used for this module — raw POST parsing instead, see below)
core/views_attachments.py → all views: list, upload, replace, preview/download, delete/restore,
                             comment, category CRUD, version history
core/urls.py               → routes for all of the above, grouped under one comment header
core/permissions.py        → new permission codenames + role grants + (if needed) a new Role
core/templates/attachments/→ one template per view
core/views_reports.py      → report functions co-located here (not in the module's own view file)
```

**Two valid patterns for form handling exist side by side** — pick whichever matches the
surrounding code you're extending, don't mix them within one view file:
1. **Raw POST parsing** (`request.POST.get('field', '').strip()`) — used by `views_facility.py`,
   `views_attachments.py`, `views_admissions.py`, and most newer modules. Preferred for new code
   because it keeps the view self-contained and matches the majority of the codebase.
2. **Django `ModelForm`** (`core/forms.py`) — used by `views_doctor.py`,`views_nursing.py`, and
   some older modules, via `SomeForm(request.POST)` / `form.save(commit=False)`.

## How to Create a New Module

1. Design the model(s) in `core/models.py`, appended near a related existing section (or under
   a new `# ══...` header at the end of the file).
2. `python manage.py makemigrations core --name <feature>` — inspect the generated file, run
   `python manage.py migrate`.
3. Add new permission codenames to `HMSPermissions.Meta.permissions` in `core/models.py`, then
   grant them to the relevant roles in `core/permissions.py`'s `ROLE_PERMISSIONS` dict. Run
   `python manage.py setup_rbac` to sync.
4. Create `core/views_<module>.py` following the structure above.
5. Add routes to `core/urls.py`, importing your new view module at the top of the file (it's
   imported once as part of the big `from . import (...)` block).
6. Create templates under `core/templates/<module>/`.
7. If the module needs its own dashboard tile, add an entry to `DASHBOARD_MODULES` in
   `core/permissions.py`.
8. Write a verification script (see `docs/TESTING.md`) exercising the full workflow, wrapped in
   `transaction.atomic()` with a forced rollback, before considering the feature done.

## How to Add Models

- Put new fields on an existing model when they're a natural extension of it (e.g. adding
  `item_code`/`generic_name` to `InventoryItem` for the Item Master recreation) rather than
  creating a parallel model.
- Use `ForeignKey(..., on_delete=PROTECT)` by default; `SET_NULL` (with `null=True, blank=True`)
  only for genuinely optional links — see `docs/DATABASE.md`'s conventions section.
- Never write a destructive `AlterField` migration by hand for a type change (e.g.
  CharField → ForeignKey) — split it into `RemoveField` + `AddField` + a `RunPython` backfill,
  as was done for the Lab Catalog's category field.

## How to Create Views

Every view that isn't a public login page starts with `@hms_permission_required('core.<perm>')`
(`core/decorators.py`) — it requires login **and** every listed permission. There's no built-in
"has permission A OR is the record owner" helper; write that check manually in the view body
when needed (see `core/views_signatures.py`'s `_can_manage()` for the pattern: an employee may
always manage their own signature, in addition to anyone holding the management permission).

## How to Create APIs

There is no API framework to plug into — see `docs/API.md`. To add a new AJAX/JSON endpoint
matching the existing convention: a `GET`-only view returning `JsonResponse({'results': [...]})`
gated by the same permission the parent page requires, consumed by inline `fetch()` in the
template. Do not introduce Django REST Framework for a single endpoint; only add it as a
dependency if you're building a genuine API surface (see `requirements.txt`).

## How to Create Reports

Every report follows one of two established patterns:

1. **The generic card-report helper** — `_render_card_report()` and the shared template
   `core/templates/reports/card_generic_report.html` in `core/views_reports.py`. Build a
   `headers` list and `rows` list of lists, call `_render_card_report(request, title, headers,
   rows, filename_prefix, from_date=..., to_date=..., ...)`. This is the preferred pattern for
   any new report — it gets CSV/Excel export and print styling for free.
2. **A bespoke template** — used by a handful of older/richer reports (e.g. the Executive
   Overview) that need custom charts/layout beyond a table.

Register the report: add the view function to `core/views_reports.py` (or the module's own
views file, gated by a `view_<module>_reports` permission), add a URL, and add an entry to the
relevant list (e.g. `admission_reports`, `attachment_reports`) inside `reports_hub()` so it
appears on `/reports/`.

## How to Add a New Role

1. Add the exact role name string to `ROLE_NAMES` in `core/permissions.py`.
2. Add a `'Role Name': {...permission set...}` entry to `ROLE_PERMISSIONS`, reusing shared
   permission-set constants (`_CLINICAL_BASE`, `_ATTACHMENT_BASE`, etc.) where the role overlaps
   with an existing one, per the `_XXX_BASE`/`_XXX_ADMIN` composition convention used throughout
   this file.
3. Add an entry to `ROLE_DEPARTMENTS` (maps the role to a default department name/type — used by
   the HR sample-data backfill, not enforced at runtime).
4. Add an entry to `ROLE_META` (badge color + one-line description, shown in the role list UI).
5. Add the role name to the relevant group in `ROLE_CATEGORIES`.
6. If it needs its own `DEPARTMENT_DEFAULTS` entry (a department that doesn't already exist),
   add `(name, dept_type)` there too.
7. Run `python manage.py setup_rbac` — it creates the Group, syncs permissions, and (if you also
   added a row to `setup_rbac.py`'s own `sample_users` list) creates a demo account.

See the **Medical Records Officer** role (added for the Attachment module) as a complete,
recent worked example across all six files above.

## How to Add New Permissions

1. Add `('codename', 'Human-readable description')` to `HMSPermissions.Meta.permissions` in
   `core/models.py`.
2. `python manage.py makemigrations core --name <descriptive_name>` — this only changes
   `Meta.permissions` metadata, always a safe/trivial migration.
3. Grant it to the relevant role(s) in `ROLE_PERMISSIONS` (`core/permissions.py`).
4. `python manage.py setup_rbac` to sync.
5. Gate the view(s) that need it with `@hms_permission_required('core.<codename>')`, and/or
   check it in a template with `{% if perms.core.<codename> %}`.

## How to Add New Settings

There is no generic "Settings" model/framework to plug into (see `docs/CONFIGURATION.md`'s Gaps
section) — the two that exist (`CardSettings`, `ServiceChargeSettings`) are both hand-built
singletons (`pk=1`, `get_or_create`). To add a new one, follow that exact pattern: a model with
a single always-`pk=1` row, a `<thing>_settings_edit` view gated by `core.system_configuration`,
and a form template — do not build a generic key-value settings table unless you're deliberately
replacing this convention project-wide.

## How to Add New Departments

No code change needed — Departments are pure runtime data. Use the UI at
`/settings/departments/create/` (`core.manage_departments`), or add a `(name, dept_type)` tuple
to `DEPARTMENT_DEFAULTS` in `core/permissions.py` if it should exist as part of every fresh
`setup_rbac` run.

## How to Add New Laboratory Tests

No code change needed for a single test — use the Lab Test Master admin UI
(`core.manage_lab_services`). For bulk import from a spreadsheet, follow the pattern in
`core/management/commands/import_lab_catalog.py`: idempotent by cleaned test code, a documented
category-cleanup mapping table, `transaction.atomic()`, and a `--dry-run` flag.

## How to Add New Inventory Items

Same story — use the Item Master UI (`core.manage_inventory`) for one-off additions, or follow
`core/management/commands/import_inventory_master.py` as the template for a bulk import: same
idempotent-by-code, documented-mapping-table, dry-run pattern as the lab importer.

## Audit Logging Convention

```python
from .audit import log_action
log_action(
    request.user, AuditLog.Action.CREATE, AuditLog.Module.DOCUMENT,
    object_type='PatientAttachment', object_id=obj.pk, object_repr=str(obj),
    description=f'Document uploaded for {patient.full_name}: "{obj.title}"',
    request=request,
)
```

Call this from every view that creates, updates, deletes, approves, rejects, or otherwise
changes state — and additionally on `VIEW`/`ACCESS` for anything confidential (see how
`patient_attachment_preview` logs an `ACCESS` event specifically when the document is
confidential). If your module introduces a genuinely new category of event, add a new
`AuditLog.Module` choice (a simple additive `TextChoices` entry, not a schema-breaking change).
