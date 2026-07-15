"""
Recreate the full Laboratory Test Master Database from a legacy LIS Excel
export (Category -> Panel -> Test tree, self-referencing via Id/PId).

Run: python manage.py import_lab_catalog --path "C:\\path\\to\\file.xlsx"

Source shape (confirmed by direct inspection of the provided workbook):
- Two sheets; `Sheet2` is the clean, canonical 412-row export (`Sheet1` is a
  stale, messier partial copy with junk rows spilled into column A — ignored).
- Self-referencing tree: a row is a root category if PId==Id or PId is
  missing from the sheet. Depth reaches up to 4 (Category -> Panel ->
  nested Panel -> Test -> reflex Test, e.g. INR nested under PT).
- ~20 rows are pure junk (blank/numeric-only name, no code, no children,
  no price) and are skipped.
- A handful of real individual tests lost their parent panel in the source
  (broken PId reference) and get manually re-homed to a sensible category
  via ORPHAN_TEST_CATEGORY_OVERRIDE below, keyed by their source Id.

Idempotent: re-running updates existing rows (matched by cleaned Code)
rather than duplicating them.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import LabCategory, LabService, LabTestResultOption

# Every category named in the spec, plus every real source root category —
# guarantees each exists even if the source has zero tests in it yet.
FULL_CATEGORY_SEED_LIST = [
    'Hematology', 'Clinical Chemistry', 'Immunology', 'Serology', 'Microbiology',
    'Parasitology', 'Urinalysis', 'Stool Examination', 'Histopathology', 'Cytology',
    'Blood Bank', 'Molecular Diagnostics', 'Hormone Tests', 'Tumor Markers',
    'Coagulation', 'Electrolytes', 'Toxicology', 'Body Fluid Analysis',
    'CSF Analysis', 'Other',
]

# Source root category name (lowercased) -> canonical LabCategory name.
ROOT_CATEGORY_MAP = {
    'hematology':          'Hematology',
    'blood chemistry':     'Clinical Chemistry',
    'urine':                'Urinalysis',
    'stool':                'Stool Examination',
    'body fluid analysis':  'Body Fluid Analysis',
    'csf analysis':         'CSF Analysis',
    'coagulation panel':    'Coagulation',
    'immuno assay':         'Immunology',
    'microbiology':         'Microbiology',
    'pathalogy':            'Histopathology',
    'serology':             'Serology',
}

# Source Id -> target category name, for real tests whose parent panel was
# lost in the source data (confirmed by direct inspection: these are
# genuine tests, not junk, but became 1-node "roots" due to a broken PId).
ORPHAN_TEST_CATEGORY_OVERRIDE = {
    137: 'Electrolytes',    # Chloride
    138: 'Electrolytes',    # Sodium
    139: 'Electrolytes',    # Potassium
    266: 'Clinical Chemistry',  # Amylase
    269: 'Clinical Chemistry',  # Glucose
    290: 'Hormone Tests',   # Free Thyroxin Index
    291: 'Hormone Tests',   # Thyroxin T4
    292: 'Hormone Tests',   # T3 Uptake
}

JUNK_LEAF_NAMES = {'1', '2', '3', '4', '5', '6', '7', '8', '9', '-', ''}

SPECIMEN_MAP = {
    'whole blood': LabService.SampleType.BLOOD,
    'serum':       LabService.SampleType.SERUM,
    'plasma':      LabService.SampleType.PLASMA,
    'urine':       LabService.SampleType.URINE,
    'stool':       LabService.SampleType.STOOL,
    'tissue':      LabService.SampleType.TISSUE,
    'csf':         LabService.SampleType.CSF,
    'sputum':      LabService.SampleType.SPUTUM,
    'swab':        LabService.SampleType.SWAB,
}


def _s(val):
    """Coerce a cell value to a stripped string, tolerating None/non-str."""
    if val is None:
        return ''
    return str(val).strip()


def _clean_result_options(raw):
    if not raw:
        return []
    text = _s(raw).replace('_x000D_\n', '\n').replace('\r\n', '\n').replace('\r', '\n')
    return [line.strip() for line in text.split('\n') if line.strip()]


class Command(BaseCommand):
    help = 'Recreate the Laboratory Test Master Database from a legacy LIS Excel export.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path', default=r'C:\Users\agi1w\Desktop\marciaLabdatabase.xlsx',
            help='Path to the source .xlsx file (default: the provided marciaLabdatabase.xlsx).',
        )
        parser.add_argument('--sheet', default='Sheet2', help='Sheet name to import (default: Sheet2, the canonical one).')
        parser.add_argument('--dry-run', action='store_true', help='Parse and report without writing to the database.')

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('openpyxl is required: pip install openpyxl')

        path = options['path']
        try:
            wb = openpyxl.load_workbook(path, data_only=True)
        except FileNotFoundError:
            raise CommandError(f'File not found: {path}')

        sheet_name = options['sheet']
        if sheet_name not in wb.sheetnames:
            raise CommandError(f'Sheet "{sheet_name}" not found. Available: {wb.sheetnames}')
        ws = wb[sheet_name]

        headers = [c.value for c in ws[1]]
        rows = [dict(zip(headers, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
        rows = [r for r in rows if isinstance(r.get('Id'), int)]
        byid = {r['Id']: r for r in rows}
        children_map = {}
        for r in rows:
            if r['PId'] != r['Id']:
                children_map.setdefault(r['PId'], []).append(r)

        def is_root(r):
            return r['PId'] == r['Id'] or r['PId'] not in byid

        roots = [r for r in rows if is_root(r)]

        self.stats = {
            'categories_created': 0, 'tests_created': 0, 'tests_updated': 0,
            'skipped_junk': 0, 'codes_autogenerated': 0, 'result_option_rows': 0,
        }
        self.category_cache = {}
        self.dry_run = options['dry_run']
        # Precompute how many source rows share each raw (cleaned) code —
        # a property of the source file alone, not of DB/run state — so the
        # decision "does this row need a -<source_id> disambiguation suffix"
        # is fully deterministic and identical on every run, keeping the
        # whole import naturally idempotent.
        self._raw_code_counts = {}
        for r in rows:
            raw = _s(r.get('Code'))[:30]
            if raw:
                self._raw_code_counts[raw] = self._raw_code_counts.get(raw, 0) + 1

        with transaction.atomic():
            if not self.dry_run:
                for name in FULL_CATEGORY_SEED_LIST:
                    self._resolve_category(name)

            for root in roots:
                self._process_root(root, children_map)

            if self.dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f"\nImport {'(DRY RUN, nothing written) ' if self.dry_run else ''}complete:\n"
            f"  Categories created:     {self.stats['categories_created']}\n"
            f"  Tests created:          {self.stats['tests_created']}\n"
            f"  Tests updated:          {self.stats['tests_updated']}\n"
            f"  Junk rows skipped:      {self.stats['skipped_junk']}\n"
            f"  Codes auto-generated:   {self.stats['codes_autogenerated']}\n"
            f"  Result-option rows:     {self.stats['result_option_rows']}\n"
        ))

    # ── Category resolution ──────────────────────────────────────────────

    def _resolve_category(self, name):
        key = name.strip()
        if key in self.category_cache:
            return self.category_cache[key]
        category, created = LabCategory.objects.get_or_create(
            name=key, defaults={'is_active': True},
        )
        if created:
            self.stats['categories_created'] += 1
        self.category_cache[key] = category
        return category

    def _category_for_root(self, root_name):
        mapped = ROOT_CATEGORY_MAP.get(root_name.strip().lower(), root_name.strip().title())
        return self._resolve_category(mapped)

    # ── Tree walk ─────────────────────────────────────────────────────────

    def _count_subtree(self, row, children_map):
        kids = [k for k in children_map.get(row['Id'], []) if k['Id'] != row['Id']]
        return 1 + sum(self._count_subtree(k, children_map) for k in kids)

    def _process_root(self, root, children_map):
        name = _s(root.get('Name'))
        kids = [k for k in children_map.get(root['Id'], []) if k['Id'] != root['Id']]

        if root['Id'] in ORPHAN_TEST_CATEGORY_OVERRIDE:
            category = self._resolve_category(ORPHAN_TEST_CATEGORY_OVERRIDE[root['Id']])
            svc = self._make_or_update_service(root, category, None)
            for kid in kids:
                self._process_node(kid, category, svc, children_map)
            return

        if not name or name in JUNK_LEAF_NAMES:
            self.stats['skipped_junk'] += self._count_subtree(root, children_map)
            return

        category = self._category_for_root(name)
        for kid in kids:
            self._process_node(kid, category, None, children_map)

    def _process_node(self, row, category, parent_service, children_map):
        kids = [k for k in children_map.get(row['Id'], []) if k['Id'] != row['Id']]
        name = _s(row.get('Name'))
        code = _s(row.get('Code'))

        if not kids and not name and not code:
            self.stats['skipped_junk'] += 1
            return
        if not kids and name in JUNK_LEAF_NAMES and not code:
            self.stats['skipped_junk'] += 1
            return

        svc = self._make_or_update_service(row, category, parent_service)
        for kid in kids:
            self._process_node(kid, category, svc, children_map)

    # ── Service creation ─────────────────────────────────────────────────

    def _clean_code(self, raw_code, source_id):
        """Resolve a stable, collision-free code for this source row. Blank
        codes get a `LAB-<source_id>` fallback; codes shared by more than one
        source row (a handful of genuine duplicates in the source file) get a
        `-<source_id>` suffix. Both paths depend only on the row's own data,
        so the same source row always resolves to the same final code and
        `update_or_create(code=...)` is naturally idempotent across re-runs."""
        code = _s(raw_code)[:30]
        if not code:
            self.stats['codes_autogenerated'] += 1
            return f'LAB-{source_id:05d}'
        if self._raw_code_counts.get(code, 1) > 1:
            return f'{code[:24]}-{source_id}'
        return code

    def _make_or_update_service(self, row, category, panel_service):
        source_id = row['Id']
        code = self._clean_code(row.get('Code'), source_id)
        name = _s(row.get('Name')) or f'Unnamed Test {source_id}'
        specimen = SPECIMEN_MAP.get(_s(row.get('DefaultSpecimenType')).lower(), LabService.SampleType.OTHER)

        try:
            price = Decimal(str(row.get('CurrentPrice') or 0))
        except (InvalidOperation, TypeError):
            price = Decimal('0')

        tat_hours = row.get('TargetTAT') or row.get('TargetTATTo') or 0
        try:
            tat_hours = int(tat_hours)
        except (TypeError, ValueError):
            tat_hours = 0

        result_options = _clean_result_options(row.get('ResultTypes'))
        result_type = LabService.ResultType.SELECT_LIST if result_options else LabService.ResultType.NUMERIC
        result_input_type = (
            LabService.ResultInputType.DROPDOWN_SELECT if result_options
            else LabService.ResultInputType.NUMERIC_ENTRY
        )

        defaults = dict(
            name=name,
            category=category,
            panel=panel_service,
            sample_type=specimen,
            unit_of_measurement=_s(row.get('TestUnitName'))[:50],
            reference_range=_s(row.get('ReferenceRange'))[:300],
            qc_reference_range=_s(row.get('QCReferenceRange'))[:300],
            critical_values=_s(row.get('CriticalValues'))[:300],
            analyzer=_s(row.get('Analyzer'))[:100],
            standard_price=price,
            turnaround_hours=tat_hours if tat_hours > 0 else 24,
            display_order=int(row.get('DisplayOrder') or 0),
            is_printable=bool(row.get('PrintedOnReport')),
            is_active=not bool(row.get('Inactive')),
            result_type=result_type,
            result_input_type=result_input_type,
            notes=_s(row.get('FootNote'))[:1000],
        )

        if self.dry_run:
            existed = LabService.objects.filter(code=code).exists()
            self.stats['tests_updated' if existed else 'tests_created'] += 1
            return None

        svc, created = LabService.objects.update_or_create(code=code, defaults=defaults)
        self.stats['tests_created' if created else 'tests_updated'] += 1

        if result_options:
            svc.result_options.all().delete()
            LabTestResultOption.objects.bulk_create([
                LabTestResultOption(lab_service=svc, value=val, display_order=i)
                for i, val in enumerate(result_options)
            ])
            self.stats['result_option_rows'] += len(result_options)

        return svc
