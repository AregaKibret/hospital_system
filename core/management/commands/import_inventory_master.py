"""
Recreate the full Inventory Item Master Database from a legacy hospital
pharmacy/store POS Excel export (flat item list, `Sheet2`).

Run: python manage.py import_inventory_master --path "C:\\path\\to\\file.xlsx"

Source shape (confirmed by direct inspection of the provided workbook):
- Two sheets; `Sheet2` is the canonical 3,845-row export (`Sheet1` is empty).
- Columns: Code, FiscalCode, ItemCategory, Name, Description, GenericName,
  StoreUnit, RetailUnit, ConsumptionUnit, SoldInConsumptionUnit,
  RetailFactor, ConsumptionFactor, MinimumQuantity, MaximumQuantity,
  Strength, DosageForm, BillCategoryId, RegistrationDate,
  TherapeuticCategory, Note, CurrentPrice, InpatientPrice, Active,
  InteractingDrugs, ExpiryStatus, StockStatus, NotGivenToPregnantWoman,
  TaxType, ShelfLocation, Exported, EmergencyPrice, Barcodes.
- `Code` has zero duplicates across all 3,845 rows -> used as the stable
  idempotency key (InventoryItem.item_code).
- `Name` is a placeholder dash for ~1,534/3,845 rows (~40%); `GenericName`
  is always populated. Resolution: when Name is blank/dash-only, `name`
  falls back to GenericName and `brand` is left blank; otherwise `name` =
  Name and `brand` = Name too. `generic_name` always gets GenericName
  regardless, so the clinical/generic descriptor is never lost.
- `ItemCategory` (32 raw values) mixes true store categories
  (Pharmaceuticals, Medical Supply/ Consumables, Supply, Lab Reagent,
  Cosmotics, Miscellaneous, others, blank) with pharma therapeutic-class
  labels (Anti-Infectives, Cardiovascular Drugs, CNS Drugs, ...). Mapped via
  ITEM_CATEGORY_MAP below into ~5 normalized InventoryCategory buckets +
  item_type; the raw string is preserved verbatim in `subcategory`.
- `TherapeuticCategory` is a *separate*, finer-grained clinical
  classification column (43 distinct values: Antibiotics, Analgesic,
  Cardiovascular drugs, ...) — imported verbatim into `therapeutic_category`
  ('N/A' / blank treated as no value).
- No stock-on-hand column exists at all (MinimumQuantity/MaximumQuantity are
  reorder policy, not current stock) -> every imported item starts at
  quantity_in_stock=0; real opening balances are entered afterward through
  the existing Opening Balance / Physical Count module, not fabricated here.
- Explicitly NOT imported (documented, not silent): ExpiryStatus/StockStatus
  (the source system's own stale point-in-time flags — this system computes
  these live from real batch data instead), NotGivenToPregnantWoman /
  InteractingDrugs / ShelfLocation / Barcodes (100% empty in this file),
  RegistrationDate / BillCategoryId / Exported / FiscalCode (source-system
  bookkeeping with no equivalent need here).
- DosageForm value 'NP' ("not applicable" marker in the source) is treated
  as blank rather than imported literally.

Idempotent: re-running updates existing rows (matched by item_code) rather
than duplicating them.
"""
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import InventoryCategory, InventoryItem, ItemGroup, UnitOfMeasure

# Category buckets the real source data populates, mapped from the raw
# (lowercased, trimmed) ItemCategory value -> (normalized category name,
# InventoryItem.ItemType).
ITEM_CATEGORY_MAP = {
    # Medications — Pharmaceuticals + every drug therapeutic-class label
    'pharmaceuticals': ('Medications', InventoryItem.ItemType.MEDICATION),
    'anti- infectives': ('Medications', InventoryItem.ItemType.MEDICATION),
    'anti-infectives': ('Medications', InventoryItem.ItemType.MEDICATION),
    'drugs used in musculoskeletal and joint disease': ('Medications', InventoryItem.ItemType.MEDICATION),
    'dermatologic agents': ('Medications', InventoryItem.ItemType.MEDICATION),
    'drugs used in anaesthesia': ('Medications', InventoryItem.ItemType.MEDICATION),
    'drugs used in anesthesia': ('Medications', InventoryItem.ItemType.MEDICATION),
    'drugs acting on the gi system': ('Medications', InventoryItem.ItemType.MEDICATION),
    'anti-histamins/ anti allergics': ('Medications', InventoryItem.ItemType.MEDICATION),
    'vitamins': ('Medications', InventoryItem.ItemType.MEDICATION),
    'blood products and drugs affecting the blood': ('Medications', InventoryItem.ItemType.MEDICATION),
    'cardiovascular drugs': ('Medications', InventoryItem.ItemType.MEDICATION),
    'cardiovascular drug': ('Medications', InventoryItem.ItemType.MEDICATION),
    'cns drugs': ('Medications', InventoryItem.ItemType.MEDICATION),
    'anti-histamin/ anti-emetic': ('Medications', InventoryItem.ItemType.MEDICATION),
    'respiratory drugs': ('Medications', InventoryItem.ItemType.MEDICATION),
    'psycotropic drugs': ('Medications', InventoryItem.ItemType.MEDICATION),
    'narcotics & psychotropics': ('Medications', InventoryItem.ItemType.MEDICATION),
    'hormone': ('Medications', InventoryItem.ItemType.MEDICATION),
    'narcotic drugs': ('Medications', InventoryItem.ItemType.MEDICATION),
    'immunological preparations': ('Medications', InventoryItem.ItemType.MEDICATION),
    'anti-spasmodic': ('Medications', InventoryItem.ItemType.MEDICATION),
    'antidotes and other substances used in poisoning': ('Medications', InventoryItem.ItemType.MEDICATION),
    # Medical Consumables
    'medical supply/ consumables': ('Medical Consumables', InventoryItem.ItemType.MEDICAL_SUPPLY),
    'supply': ('Medical Consumables', InventoryItem.ItemType.MEDICAL_SUPPLY),
    'supplies': ('Medical Consumables', InventoryItem.ItemType.MEDICAL_SUPPLY),
    # Laboratory Reagents
    'lab reagent': ('Laboratory Reagents', InventoryItem.ItemType.LAB_SUPPLY),
    'reagent': ('Laboratory Reagents', InventoryItem.ItemType.LAB_SUPPLY),
    # Cosmetics & Toiletries
    'cosmotics': ('Cosmetics & Toiletries', InventoryItem.ItemType.GENERAL),
    # Miscellaneous / Other (+ blank)
    'miscellaneous': ('Miscellaneous / Other', InventoryItem.ItemType.OTHER),
    'others': ('Miscellaneous / Other', InventoryItem.ItemType.OTHER),
    '': ('Miscellaneous / Other', InventoryItem.ItemType.OTHER),
}

# Spec-suggested taxonomy pre-created as empty, admin-ready shells (0 items
# from this import — the source data doesn't distinguish these).
EMPTY_CATEGORY_SHELLS = [
    'Surgical Supplies', 'Radiology Supplies', 'Dental Supplies', 'Orthopedic Supplies',
    'Ward Supplies', 'Emergency Supplies', 'ICU Supplies', 'Operating Room Supplies',
    'Office Supplies', 'Cleaning Supplies', 'Medical Equipment', 'Non-Medical Equipment',
]

PLACEHOLDER_CHARS = set('- ')


def _s(val):
    """Coerce a cell value to a stripped string, tolerating None/non-str."""
    if val is None:
        return ''
    return str(val).strip()


def _clean_unit(val):
    """Strip whitespace and Excel line-break encoding artifacts (e.g.
    'Vial_x000D_\\n', 'Vial  ') down to a clean unit label."""
    text = _s(val).replace('_x000D_\n', '').replace('\r\n', '').replace('\r', '').replace('\n', '')
    return text.strip()


def _is_placeholder_name(val):
    text = _s(val)
    return not text or set(text) <= PLACEHOLDER_CHARS


def _decimal(val, default='0'):
    try:
        if val in (None, ''):
            return Decimal(default)
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


class Command(BaseCommand):
    help = 'Recreate the Inventory Item Master Database from a legacy pharmacy/store Excel export.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path', default=r'C:\Users\agi1w\Downloads\marciaitem.xlsx',
            help='Path to the source .xlsx file (default: the provided marciaitem.xlsx).',
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
        rows = [r for r in rows if _s(r.get('Code'))]

        self.dry_run = options['dry_run']
        self.stats = {
            'items_created': 0, 'items_updated': 0, 'skipped_no_code': 0,
            'categories_created': 0, 'units_created': 0,
        }
        self.category_cache = {}
        self.unit_cache = set()

        with transaction.atomic():
            for name in EMPTY_CATEGORY_SHELLS:
                self._resolve_category(name)

            distinct_units = set()
            for row in rows:
                for col in ('StoreUnit', 'RetailUnit', 'ConsumptionUnit'):
                    u = _clean_unit(row.get(col))
                    if u:
                        distinct_units.add(u)
            for u in sorted(distinct_units):
                self._resolve_unit(u)

            for row in rows:
                self._import_row(row)

            if self.dry_run:
                transaction.set_rollback(True)

        self.stdout.write(self.style.SUCCESS(
            f"\nImport {'(DRY RUN, nothing written) ' if self.dry_run else ''}complete:\n"
            f"  Items created:        {self.stats['items_created']}\n"
            f"  Items updated:        {self.stats['items_updated']}\n"
            f"  Rows skipped (no code): {self.stats['skipped_no_code']}\n"
            f"  Categories created:   {self.stats['categories_created']}\n"
            f"  Units created:        {self.stats['units_created']}\n"
        ))

    # ── Lookups ───────────────────────────────────────────────────────────

    def _resolve_category(self, name):
        key = name.strip()
        if key in self.category_cache:
            return self.category_cache[key]
        category, created = InventoryCategory.objects.get_or_create(name=key)
        if created:
            self.stats['categories_created'] += 1
        self.category_cache[key] = category
        return category

    def _resolve_unit(self, name):
        if name in self.unit_cache:
            return
        _, created = UnitOfMeasure.objects.get_or_create(name=name)
        if created:
            self.stats['units_created'] += 1
        self.unit_cache.add(name)

    # ── Row import ────────────────────────────────────────────────────────

    def _import_row(self, row):
        code = _s(row.get('Code'))[:30]
        if not code:
            self.stats['skipped_no_code'] += 1
            return

        raw_name = _s(row.get('Name'))
        generic_name = _s(row.get('GenericName'))
        if not _is_placeholder_name(raw_name):
            name = raw_name
            brand = raw_name
        elif not _is_placeholder_name(generic_name):
            name = generic_name
            brand = ''
        else:
            # Both Name and GenericName are blank/dash placeholders in the
            # source for this row — fall back to the item code so the item
            # still has a meaningful, searchable name.
            name = code
            brand = ''

        raw_category = _s(row.get('ItemCategory'))
        cat_key = raw_category.lower()
        cat_name, item_type = ITEM_CATEGORY_MAP.get(cat_key, ('Miscellaneous / Other', InventoryItem.ItemType.OTHER))
        category = self._resolve_category(cat_name)

        therapeutic = _s(row.get('TherapeuticCategory'))
        if therapeutic.upper() in ('N/A', ''):
            therapeutic = ''

        dosage_form = _s(row.get('DosageForm'))
        if dosage_form.upper() == 'NP':
            dosage_form = ''

        min_qty = _decimal(row.get('MinimumQuantity'))
        max_qty = _decimal(row.get('MaximumQuantity'))

        defaults = dict(
            name=name[:200],
            generic_name=generic_name[:255],
            brand=brand[:100],
            item_type=item_type,
            category=category,
            subcategory=raw_category[:100],
            therapeutic_category=therapeutic[:100],
            dosage_form=dosage_form[:50],
            strength=_s(row.get('Strength'))[:100],
            description=_s(row.get('Description')),
            unit=_clean_unit(row.get('StoreUnit'))[:50] or 'units',
            unit_purchase=_clean_unit(row.get('RetailUnit'))[:50],
            units_per_purchase=_decimal(row.get('RetailFactor'), '1') or Decimal('1'),
            dispensing_unit=_clean_unit(row.get('ConsumptionUnit'))[:50],
            consumption_factor=_decimal(row.get('ConsumptionFactor'), '1') or Decimal('1'),
            min_stock=min_qty,
            reorder_level=min_qty,
            max_stock=max_qty if max_qty > 0 else None,
            selling_price=_decimal(row.get('CurrentPrice')),
            inpatient_price=_decimal(row.get('InpatientPrice')),
            emergency_price=_decimal(row.get('EmergencyPrice')),
            tax_type=_s(row.get('TaxType'))[:20],
            is_active=bool(row.get('Active')),
        )

        if self.dry_run:
            existed = InventoryItem.objects.filter(item_code=code).exists()
            self.stats['items_updated' if existed else 'items_created'] += 1
            return

        _, created = InventoryItem.objects.update_or_create(item_code=code, defaults=defaults)
        self.stats['items_created' if created else 'items_updated'] += 1
