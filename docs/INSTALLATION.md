# Installation Guide

Complete setup instructions for Windows, Ubuntu Linux, and macOS. All three follow the same
logical steps — install prerequisites, clone, create a virtual environment, install
dependencies, configure `.env`, migrate, seed roles, run.

> There is no Node.js/npm step anywhere in this guide — the frontend is plain Django templates
> with Tailwind CSS loaded from a CDN, with no build pipeline.

---

## Windows

### 1. Install Python

Download Python 3.12+ from [python.org](https://www.python.org/downloads/windows/). During
install, check **"Add Python to PATH."** Verify:

```powershell
python --version
```

### 2. Install Git

Download from [git-scm.com](https://git-scm.com/download/win). Verify:

```powershell
git --version
```

### 3. Install PostgreSQL

Download the installer from [postgresql.org](https://www.postgresql.org/download/windows/)
(this project was developed and tested against PostgreSQL 18). During install:
- Set a password for the `postgres` superuser and remember it — you'll need it for `.env`.
- Keep the default port `5432`.
- The installer includes pgAdmin (optional GUI) and adds `psql` to your PATH under
  `C:\Program Files\PostgreSQL\<version>\bin`.

Create the application database and role:

```powershell
psql -U postgres
```
```sql
CREATE DATABASE hospital_db;
CREATE USER hospital_user WITH PASSWORD 'choose-a-strong-password';
GRANT ALL PRIVILEGES ON DATABASE hospital_db TO hospital_user;
\q
```

### 4. Clone the Project

```powershell
git clone https://github.com/AregaKibret/hospital_system.git
cd hospital_system
```

### 5. Create a Virtual Environment

```powershell
python -m venv venv
venv\Scripts\activate
```

### 6. Install Dependencies

```powershell
pip install -r requirements.txt
# Optional, for testing/linting tools:
pip install -r requirements-dev.txt
```

### 7. Configure Environment Variables

```powershell
copy .env.example .env
notepad .env
```

Fill in `SECRET_KEY` (generate one — see `docs/CONFIGURATION.md`), and either `DATABASE_URL`
or the individual `DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT` matching what you
created in Step 3.

### 8. Run Database Migrations

```powershell
python manage.py migrate
```

### 9. Seed Initial Data

```powershell
python manage.py setup_rbac
```

This creates all ~35 roles with their permissions and ~35 sample users — one per role — all
with password `Test@1234`. **It does not check `DEBUG` or otherwise skip this in production** —
running it against a live deployment creates the same predictable accounts there too. See
`docs/SECURITY.md` for the recommended mitigation. Optionally seed sample clinical/inventory
data:

```powershell
python manage.py seed_data
```

### 10. Create an Administrator Account

`setup_rbac` already creates a `sysadmin` account (Administrator role, password `Test@1234`).
To create your own real superuser instead/as well:

```powershell
python manage.py createsuperuser
```

Then, from the running application, log in as `sysadmin` (or your superuser) and use
**User Management** to assign the `Administrator` role to any additional real accounts, or
edit them via `python manage.py shell`.

### 11. Run the Development Server

```powershell
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`.

---

## Linux (Ubuntu)

### 1. Install Prerequisites

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git postgresql postgresql-contrib libpq-dev
```

### 2. Create the Database

```bash
sudo -u postgres psql
```
```sql
CREATE DATABASE hospital_db;
CREATE USER hospital_user WITH PASSWORD 'choose-a-strong-password';
GRANT ALL PRIVILEGES ON DATABASE hospital_db TO hospital_user;
\q
```

### 3. Clone the Project

```bash
git clone https://github.com/AregaKibret/hospital_system.git
cd hospital_system
```

### 4. Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional
```

### 6. Configure Environment Variables

```bash
cp .env.example .env
nano .env
```

Fill in `SECRET_KEY` and your database credentials from Step 2.

### 7. Migrate, Seed, Create Admin, Run

```bash
python manage.py migrate
python manage.py setup_rbac
python manage.py createsuperuser        # optional, in addition to sysadmin
python manage.py runserver 0.0.0.0:8000
```

For a real (non-development) Linux deployment, see `docs/DEPLOYMENT.md` for running under
Gunicorn instead of the development server.

---

## macOS

### 1. Install Prerequisites (via Homebrew)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"  # if Homebrew isn't installed
brew install python@3.12 git postgresql@18
brew services start postgresql@18
```

### 2. Create the Database

```bash
psql postgres
```
```sql
CREATE DATABASE hospital_db;
CREATE USER hospital_user WITH PASSWORD 'choose-a-strong-password';
GRANT ALL PRIVILEGES ON DATABASE hospital_db TO hospital_user;
\q
```

### 3. Clone, Virtual Environment, Dependencies

```bash
git clone https://github.com/AregaKibret/hospital_system.git
cd hospital_system
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # optional
```

### 4. Configure, Migrate, Seed, Run

```bash
cp .env.example .env
nano .env    # fill in SECRET_KEY and DB credentials
python manage.py migrate
python manage.py setup_rbac
python manage.py createsuperuser        # optional
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`.

---

## Verifying the Installation

Regardless of platform:

```bash
python manage.py check          # should report "System check identified no issues"
python manage.py showmigrations # confirm every migration shows [X]
```

Log in at `/accounts/login/` with `sysadmin` / `Test@1234` (development only — see
`README.md`'s Default Login Accounts section) and confirm the dashboard loads with every
module tile visible.

See `docs/TROUBLESHOOTING.md` if any step fails.
