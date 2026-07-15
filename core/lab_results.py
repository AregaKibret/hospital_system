"""
Dynamic Laboratory Result Entry engine.

Result forms are never free text: the set of fields shown for a lab order,
their input widget, unit, and reference range are all derived from the
ordered LabService's catalog definition (its component analytes if it's a
panel, or itself if it's a standalone test). This module holds the pure
logic — building the per-order result-row set and interpreting an entered
value against the analyte's reference range — kept separate from views so
it's independently testable and reusable (result entry, print, reports).
"""
import re
from datetime import date

from django.utils import timezone

from .models import LabReferenceRange, LabResultEntry, LabService

_RANGE_PATTERNS = [
    (re.compile(r'^\s*([\-+]?\d+(?:\.\d+)?)\s*-\s*([\-+]?\d+(?:\.\d+)?)\s*$'), 'between'),
    (re.compile(r'^\s*[<≤]=?\s*([\-+]?\d+(?:\.\d+)?)\s*$'), 'max'),
    (re.compile(r'^\s*[>≥]=?\s*([\-+]?\d+(?:\.\d+)?)\s*$'), 'min'),
]

_ABNORMAL_WORDS = {'positive', 'reactive', 'present', 'abnormal', 'trace'}
_NORMAL_WORDS = {'negative', 'non-reactive', 'nonreactive', 'absent', 'normal'}


def parse_numeric_range(range_text):
    """Best-effort parse of a free-text *reference* range into a (low, high)
    envelope tuple of floats — either bound may be None. "<220" means
    "normal is below 220" (upper bound), ">190" means "normal is above 190"
    (lower bound). Returns None if the text doesn't match a recognized shape
    (source data is inconsistently formatted legacy text)."""
    if not range_text:
        return None
    text = range_text.strip()
    for pattern, kind in _RANGE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if kind == 'between':
            return (float(m.group(1)), float(m.group(2)))
        if kind == 'max':
            return (None, float(m.group(1)))
        if kind == 'min':
            return (float(m.group(1)), None)
    return None


def parse_critical_condition(critical_text):
    """Best-effort parse of a free-text *critical values* condition — unlike
    a reference range, "<40" here means "flag critical if the value is below
    40" (40 is a critical LOW threshold), and ">500" means "flag critical if
    above 500" (a critical HIGH threshold) — the literal comparison
    direction, not a normal-range envelope. Returns a callable(num) -> bool,
    or None if unparseable."""
    if not critical_text:
        return None
    text = critical_text.strip()
    for pattern, kind in _RANGE_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if kind == 'between':
            low, high = float(m.group(1)), float(m.group(2))
            return lambda num: num < low or num > high
        if kind == 'max':
            threshold = float(m.group(1))
            return lambda num: num < threshold
        if kind == 'min':
            threshold = float(m.group(1))
            return lambda num: num > threshold
    return None


def age_years(patient):
    if not patient or not patient.date_of_birth:
        return None
    today = date.today()
    dob = patient.date_of_birth
    if dob > today:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def get_effective_range(analyte, patient=None):
    """Resolve the reference_range/critical_values that apply to this
    analyte for this patient — an age/sex-specific LabReferenceRange row if
    one matches, else the analyte's own plain fields."""
    if patient is not None:
        years = age_years(patient)
        sex = (patient.sex or '').lower()
        for override in analyte.reference_ranges.all():
            if override.matches(years, sex):
                return override.reference_range, override.critical_values
    return analyte.reference_range, analyte.critical_values


def interpret_value(analyte, value, patient=None):
    """Compare an entered value against the analyte's effective reference
    range and return (flag, is_critical). Numeric types get a real
    range-bound comparison; qualitative types get a best-effort keyword
    match against critical_values / known abnormal-implying words. Returns
    ('', False) when the value or range can't be interpreted — the
    technician's judgment (manual critical override) always still applies
    on top of this."""
    reference_range, critical_values = get_effective_range(analyte, patient)
    value = (value or '').strip()
    if not value:
        return '', False

    numeric_types = (LabService.ResultInputType.NUMERIC_ENTRY, LabService.ResultInputType.DECIMAL_ENTRY)
    if analyte.result_input_type in numeric_types:
        try:
            num = float(value)
        except ValueError:
            return '', False

        is_critical_cond = parse_critical_condition(critical_values)
        if is_critical_cond and is_critical_cond(num):
            return LabResultEntry.Flag.CRITICAL, True

        bounds = parse_numeric_range(reference_range)
        if bounds:
            low, high = bounds
            if low is not None and num < low:
                return LabResultEntry.Flag.LOW, False
            if high is not None and num > high:
                return LabResultEntry.Flag.HIGH, False
            return LabResultEntry.Flag.NORMAL, False
        return '', False

    # Qualitative: best-effort keyword interpretation.
    value_lower = value.lower()
    if critical_values and value_lower in critical_values.lower():
        return LabResultEntry.Flag.CRITICAL, True
    if value_lower in _ABNORMAL_WORDS:
        return LabResultEntry.Flag.ABNORMAL, False
    if value_lower in _NORMAL_WORDS:
        return LabResultEntry.Flag.NORMAL, False
    return '', False


def get_analytes_for_service(lab_service):
    """The list of catalog rows a result form should show for an ordered
    service — its component tests if it's a panel, else itself."""
    if lab_service is None:
        return []
    children = list(lab_service.panel_tests.select_related('category').order_by('display_order', 'name'))
    return children if children else [lab_service]


def ensure_result_entries(order):
    """Idempotently create the LabResultEntry rows this order needs, one per
    analyte from its ordered service's catalog definition. Safe to call on
    every view of the order — existing rows are left untouched."""
    if not order.lab_service:
        return LabResultEntry.objects.none()

    analytes = get_analytes_for_service(order.lab_service)
    existing_ids = set(order.result_entries.values_list('analyte_id', flat=True))
    missing = [a for a in analytes if a.id not in existing_ids]
    if missing:
        patient = order.visit.patient
        LabResultEntry.objects.bulk_create([
            LabResultEntry(
                lab_order=order, analyte=analyte,
                unit=analyte.unit_of_measurement,
                reference_range=get_effective_range(analyte, patient)[0],
                display_order=analyte.display_order,
            )
            for analyte in missing
        ])
    return order.result_entries.select_related('analyte').order_by('display_order', 'id')


def save_result_entries(order, submitted, user):
    """Apply technician-submitted values to this order's result entries.
    `submitted` maps analyte_id (str or int) -> {'value': str, 'comments': str}.
    Recomputes each row's flag/is_critical, and rolls up
    order.is_critical = any entry is critical (the technician's manual
    "Mark as Critical" checkbox on the order can still add to this, never
    remove an auto-detected critical flag)."""
    patient = order.visit.patient
    entries = list(order.result_entries.select_related('analyte'))
    any_critical = False
    now = timezone.now()

    for entry in entries:
        data = submitted.get(str(entry.analyte_id))
        if data is None:
            continue
        value = (data.get('value') or '').strip()
        comments = (data.get('comments') or '').strip()
        if not value and not comments and not entry.value:
            continue
        flag, is_critical = interpret_value(entry.analyte, value, patient)
        entry.value = value
        entry.comments = comments
        entry.flag = flag
        entry.is_critical = is_critical
        entry.entered_by = user
        entry.entered_at = now
        entry.save()
        any_critical = any_critical or is_critical

    return entries, any_critical


def build_result_summary_text(order):
    """Plain-text join of all entered results — kept on LabOrder.result so
    every existing consumer that reads that field (EMR visit view, older
    reports) keeps working without modification."""
    lines = []
    for entry in order.result_entries.select_related('analyte').order_by('display_order', 'id'):
        if not entry.value:
            continue
        flag_suffix = f" [{entry.get_flag_display()}]" if entry.flag and entry.flag != LabResultEntry.Flag.NORMAL else ''
        unit = f" {entry.unit}" if entry.unit else ''
        lines.append(f"{entry.analyte.name}: {entry.value}{unit}{flag_suffix}")
    return '\n'.join(lines)
