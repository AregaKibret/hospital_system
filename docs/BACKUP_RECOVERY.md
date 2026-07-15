# Backup & Recovery

## There Is No In-Application Backup Feature

Confirmed by direct inspection of `core/management/commands/` — no command performs a database
or media backup/restore, no scheduled job exists, and no UI button triggers one. Protecting
hospital data is entirely an **operational** (infrastructure-team) responsibility today, not
something the application does for you. This guide documents the recommended external
procedure.

## What Needs Backing Up

1. **The PostgreSQL database** — every clinical, administrative, and financial record.
2. **The `media/` directory** — uploaded employee signatures and patient attachments. A
   database backup **does not** include these; they must be backed up separately, and a
   database restore **does not** bring them back on its own.

## Database Backup

### Manual, one-off

```bash
pg_dump -Fc -h <host> -U <user> -d hospital_db -f hospital_db_$(date +%Y%m%d_%H%M%S).dump
```

`-Fc` (custom format) is recommended over plain SQL — it's compressed and supports selective/
parallel restore.

### Scheduled

No scheduling exists in this project (no `cron`, no Celery, no `django-crontab` dependency —
see `docs/CONFIGURATION.md`'s gaps). Set up scheduled backups **outside the application**:

- **If hosted on Render.com** (the supported deployment path — see `docs/DEPLOYMENT.md`): use
  Render's own managed-database automated backup/point-in-time-restore feature rather than
  building your own — it requires no code or cron job in this project at all.
- **If self-hosting PostgreSQL**: a plain `cron` entry running the `pg_dump` command above on a
  schedule, writing to storage physically separate from the database server (a different disk,
  or synced to object storage) — a backup that lives on the same disk as the database it backs
  up does not protect against disk failure.

## Media Backup

Since `MEDIA_ROOT` is local disk (see `docs/CONFIGURATION.md`), back it up with a plain file-level
tool:

```bash
tar -czf media_backup_$(date +%Y%m%d).tar.gz media/
```

or sync it to object storage (`rclone`, `aws s3 sync`, etc.) on the same schedule as your
database backups, so a restore always has a matching pair. If deploying on Render, confirm
your plan has a **persistent disk** attached — see the caution in `docs/DEPLOYMENT.md`, as
`media/` can otherwise be lost on redeploy.

## Restore Process

### Database

```bash
# For a -Fc custom-format dump:
pg_restore -h <host> -U <user> -d hospital_db --clean --if-exists hospital_db_YYYYMMDD.dump

# For a plain SQL dump:
psql -h <host> -U <user> -d hospital_db -f hospital_db_YYYYMMDD.sql
```

Then point the application at the restored database (`DATABASE_URL` or the `DB_*` variables —
see `docs/CONFIGURATION.md`) and run:

```bash
python manage.py migrate      # confirm the restored DB is at the current migration state
python manage.py check
```

### Media

```bash
tar -xzf media_backup_YYYYMMDD.tar.gz -C /path/to/hospital_system/
```

Restore into the same `media/` path the application expects (`MEDIA_ROOT`, fixed at
`<project root>/media/`).

## Disaster Recovery Procedure

1. Provision a fresh application instance (new Render service, new VM, etc.) from this
   repository, following `docs/INSTALLATION.md` or `docs/DEPLOYMENT.md`.
2. Restore the most recent database dump (above) into the new database.
3. Restore the most recent `media/` archive alongside it.
4. Update DNS/`ALLOWED_HOSTS` to point at the new instance if the hostname changed.
5. Run `python manage.py check` and log in as an Administrator to confirm patient records,
   uploaded documents, and signatures are all present and correctly linked before declaring
   recovery complete.
6. Review the Audit Log (`/audit/logs/`) for the outage window once recovered, to identify any
   activity that may need to be manually reconciled if it occurred against a stale backup.

Because there is no automated backup schedule built into the application itself, your actual
recovery-point objective (RPO) is entirely a function of how frequently your external backup
job (above) runs — verify that cadence matches your hospital's tolerance for data loss before
relying on this procedure.
