# Testing Documentation

## Current State: No Test Suite Exists

`core/tests.py` is the unmodified default Django stub:

```python
from django.test import TestCase

# Create your tests here.
```

There are no unit tests, integration tests, end-to-end tests, or coverage configuration
anywhere in this project. This is a real gap, not an oversight to work around — treat this
document as "how to start testing this project" rather than "how to run the existing suite."

## How Verification Has Actually Been Done To Date

Every feature in this codebase has instead been verified with **one-off, throwaway scripts**
run through `python manage.py shell`, following this pattern:

```python
from django.db import transaction
from django.test import Client
from django.contrib.auth import get_user_model
from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')  # required or Django raises DisallowedHost

class Rollback(Exception):
    pass

try:
    with transaction.atomic():
        c = Client()
        assert c.login(username='bekele.haile', password='Test@1234')
        # ... exercise real views against real seeded RBAC users ...
        # ... assert on real database state ...
        raise Rollback()   # force everything above to roll back — nothing persists
except Rollback:
    pass
```

This is a legitimate way to verify a feature end-to-end (it drives real views, real permission
checks, and real database writes/rollbacks), but it is **not** a substitute for a real,
repeatable, checked-in test suite — nothing above is saved anywhere, so nothing prevents a
future change from silently breaking what was verified this way.

## Recommended: Adopt Django's Built-In Test Runner

No new dependency required — this works today with zero additional installation:

```bash
python manage.py test core
```

Django's `TestCase` wraps every test in a transaction and rolls it back automatically (the same
idea as the manual pattern above, but checked in and repeatable). A first real test, replacing
`core/tests.py`'s stub:

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from core.models import Patient

class PatientRegistrationTests(TestCase):
    def test_patient_gets_a_card_number_on_save(self):
        patient = Patient.objects.create(first_name='Test', last_name='Patient', sex='Male')
        self.assertTrue(patient.card_number.startswith('PAT-'))
```

## Recommended: Adopt pytest-django (Optional, Listed in `requirements-dev.txt`)

If the team prefers pytest's syntax/fixtures over `unittest`-style `TestCase`:

```bash
pip install -r requirements-dev.txt
```

Add a `pytest.ini` (does not exist yet):

```ini
[pytest]
DJANGO_SETTINGS_MODULE = hospital_system.settings
python_files = tests.py test_*.py *_tests.py
```

Then:

```bash
pytest
```

## Coverage Reports

`coverage` is listed in `requirements-dev.txt` but not yet configured. Once real tests exist:

```bash
coverage run --source='core' manage.py test core
coverage report      # terminal summary
coverage html        # browsable HTML report in htmlcov/
```

## Suggested Priority Order for a First Real Test Suite

Given the size of this codebase, start with the highest-value, lowest-effort tests first:

1. **Model-level tests** for every auto-generated numbering scheme (`Invoice`, `Prescription`,
   `PurchaseOrder`, etc. — see `docs/DATABASE.md`'s numbering table) — cheap to write, catches
   regressions in a pattern used by a dozen+ models.
2. **Permission-gating tests** — for each new feature, one test per role confirming it can/can't
   reach the view, using the `Client`-based pattern above but as a real `TestCase`.
3. **Workflow tests** for the highest-risk pipelines: Admission deposit-gating, Invoice item
   payment/credit/refund state transitions, Physical Count variance-adjustment posting.
4. **Migration tests** (`python manage.py makemigrations --check --dry-run`) in CI, to catch an
   uncommitted model change before it reaches another environment.

There is no CI pipeline (no `.github/workflows/`, no other CI config) wired up to run any of
this automatically yet — that would be the natural next step once a real test suite exists.
