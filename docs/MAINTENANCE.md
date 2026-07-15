# Maintenance Guide

Routine tasks for keeping this application running smoothly, with the actual commands this
project uses (not generic Django boilerplate).

## Updating Dependencies

```bash
pip list --outdated                        # see what's behind
pip install --upgrade <package>
pip freeze > requirements.txt               # or hand-edit the version pin, project convention
                                             # uses >= constraints, e.g. Django>=6.0,<7.0
python manage.py check                      # confirm nothing broke
python manage.py test core                  # once a real test suite exists — see docs/TESTING.md
```

Only 7 runtime dependencies exist today (`requirements.txt`): Django, psycopg2-binary,
python-decouple, openpyxl, gunicorn, whitenoise, dj-database-url — a small, easy-to-audit
surface. Re-verify the whole application manually after a Django major/minor version bump in
particular (Django 6→7 when it's released), since there is no automated test suite to catch a
breaking change for you yet.

## Applying Migrations

```bash
python manage.py showmigrations core   # see what's pending
python manage.py migrate               # apply everything pending
```

After pulling new code, **always** run `migrate` before `runserver`/restarting Gunicorn — the
application will throw errors on any page touching a model whose migration hasn't been applied.

## Clearing Cache

There is no cache framework configured (no `CACHES` setting, no Redis/Memcached) — there is
nothing to clear. If a page appears to be showing stale data, it's a database/query-logic issue,
not a cache invalidation issue.

## Rotating Logs

No custom logging configuration exists (`docs/CONFIGURATION.md`) — Django's default logging
writes to stderr/console. Log rotation is therefore your process manager's/hosting platform's
responsibility (e.g. Render aggregates and rotates service logs for you automatically; if
self-hosting under `systemd`, route Gunicorn's stdout/stderr through `journald` or a tool like
`logrotate` at the OS level).

## Monitoring Storage

Two things grow without bound over time and are worth watching:

1. **The `media/` directory** — every patient attachment and employee signature accumulates
   here (up to 25 MB / 2 MB per file respectively). Check disk usage periodically:
   ```bash
   du -sh media/
   ```
2. **The `core_auditlog` table** — every significant action in the system writes a row here,
   and nothing currently prunes/archives old entries. Check its size:
   ```sql
   SELECT pg_size_pretty(pg_total_relation_size('core_auditlog'));
   SELECT count(*) FROM core_auditlog;
   ```
   If retention becomes a concern, decide on a retention policy (e.g. archive-and-delete
   `AuditLog` rows older than N years to a cold-storage table) — no such policy exists today.

## Monitoring Database Performance

No APM/monitoring tool is integrated into this project. At minimum, periodically check for slow
queries directly in PostgreSQL:

```sql
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
```

(requires the `pg_stat_statements` extension enabled on your Postgres instance — check with
your hosting provider if self-managed, or enable it yourself if you manage the database).
`docs/DATABASE.md` documents every explicit index currently defined — a genuinely slow report
query is the first place to look for a missing index.

## Updating System Configuration

The only two in-app settings screens (`docs/CONFIGURATION.md`) — Card Settings and Service
Charge Settings — are edited through the UI directly (`core.system_configuration` permission),
no restart required. Everything else configurable lives in `.env` or `settings.py` and requires
an application restart (or a redeploy, in production) to take effect.

## Upgrading the Application

```bash
git pull origin main
pip install -r requirements.txt
python manage.py migrate
python manage.py setup_rbac        # syncs any newly added roles/permissions — safe to re-run
python manage.py collectstatic --noinput
# restart the application process (systemd service restart, or a new Render deploy)
```

See `CHANGELOG.md`'s "Upgrade Instructions" for the same steps and a reminder that no migration
in this project's history to date has been destructive.

## Common Management Commands Reference

| Command | Purpose |
|---|---|
| `python manage.py setup_rbac` | (Re)create all roles/permissions and sample users — safe to re-run any time |
| `python manage.py seed_data` | Seed general sample clinical/store data |
| `python manage.py setup_hospital_data` | Seed a small hardcoded set of departments/inventory/POs |
| `python manage.py setup_doctor_data` | Seed sample doctors/specializations |
| `python manage.py setup_surgery_data` | Seed sample surgery/procedure master data |
| `python manage.py setup_med_inventory_data` | Seed sample medications/suppliers/batches |
| `python manage.py setup_dept_pharmacy_data` | Seed sample department stores |
| `python manage.py import_lab_catalog --path <file.xlsx> [--dry-run]` | Idempotent bulk import of the Lab Test Master from an Excel export |
| `python manage.py import_inventory_master --path <file.xlsx> [--dry-run]` | Idempotent bulk import of the Inventory Item Master from an Excel export |
| `python manage.py backfill_item_payment_status` | One-time historical data-repair command (see its docstring before running against a database that's already had it applied) |
