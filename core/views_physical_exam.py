"""Views for the structured Physical Examination module."""
import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit import log_action
from .decorators import hms_permission_required
from .models import AuditLog, ClinicalNote, PhysicalExamination, PhysicalExamTemplate, Visit, VitalSign


# ── System catalogue ─────────────────────────────────────────────────────────
# Each entry: (key, label, group, quick_normal, quick_findings)
EXAM_SYSTEMS = [
    # General
    ('general_appearance',  'General Appearance',      'General',         'Alert, well-nourished, in no acute distress',
     ['Alert and oriented', 'Well-nourished', 'Well-developed', 'No acute distress',
      'Appears ill', 'In mild distress', 'In moderate distress', 'In severe distress',
      'Cachectic', 'Obese', 'Diaphoretic', 'Pallor noted']),

    ('consciousness',       'Level of Consciousness',  'General',         'Alert, GCS 15, oriented to person, place, and time',
     ['Alert', 'GCS 15', 'Oriented x3', 'Drowsy', 'Confused', 'Somnolent',
      'Obtunded', 'Stuporous', 'Comatose', 'Disoriented', 'Agitated']),

    ('mental_status',       'Mental Status',           'General',         'Normal affect, thought process intact, appropriate',
     ['Appropriate mood and affect', 'Thought process intact', 'Good judgment and insight',
      'Anxious', 'Depressed affect', 'Agitated', 'Flat affect', 'Confused',
      'Disorganized thinking', 'Paranoid ideation']),

    ('nutritional_status',  'Nutritional Status',      'General',         'Well-nourished',
     ['Well-nourished', 'Adequately nourished', 'Mildly malnourished', 'Moderately malnourished',
      'Severely malnourished', 'Cachectic', 'Obese', 'Overweight']),

    ('hydration_status',    'Hydration Status',        'General',         'Well hydrated, mucous membranes moist',
     ['Well hydrated', 'Mucous membranes moist', 'Mildly dehydrated', 'Moderately dehydrated',
      'Severely dehydrated', 'Dry mucous membranes', 'Skin turgor reduced', 'Sunken eyes']),

    ('pain_assessment',     'Pain Assessment',         'General',         'No pain reported (VAS 0/10)',
     ['No pain (0/10)', 'Mild pain 1–3/10', 'Moderate pain 4–6/10', 'Severe pain 7–9/10',
      'Worst pain 10/10', 'Acute pain', 'Chronic pain', 'Pain well controlled',
      'Pain localized', 'Pain radiating']),

    # Skin
    ('skin',                'Skin',                    'Skin & Integument', 'Warm, dry, intact; normal colour; no rash, lesions, or jaundice',
     ['Warm and dry', 'Intact', 'Normal colour', 'No rash', 'No lesions',
      'Jaundice', 'Pallor', 'Cyanosis', 'Diaphoretic', 'Petechiae',
      'Purpura', 'Ecchymosis', 'Rash present', 'Ulcer(s)', 'Erythema',
      'Oedema', 'Poor turgor', 'Wound noted']),

    # HEENT
    ('head',                'Head',                    'HEENT',            'Normocephalic, atraumatic',
     ['Normocephalic', 'Atraumatic', 'Soft anterior fontanelle', 'Scalp normal',
      'Microcephaly', 'Macrocephaly', 'Tenderness on palpation', 'Deformity noted']),

    ('eyes',                'Eyes',                    'HEENT',            'PERRL, EOM intact, conjunctivae clear, no icterus',
     ['PERRL', 'Equal and reactive pupils', 'EOM intact', 'Conjunctivae pink', 'Sclerae white',
      'No icterus', 'No nystagmus', 'Visual acuity normal', 'Conjunctival injection',
      'Scleral icterus', 'Ptosis', 'Exophthalmos', 'Papilloedema', 'Cataracts',
      'Nystagmus present', 'Anisocoria']),

    ('ears',                'Ears',                    'HEENT',            'TMs intact bilaterally, no discharge; hearing grossly intact',
     ['TMs intact bilaterally', 'No discharge', 'Hearing grossly intact', 'No erythema',
      'Cerumen impaction', 'Otitis media', 'Perforated TM', 'Decreased hearing',
      'Tenderness on palpation', 'Discharge present']),

    ('nose',                'Nose',                    'HEENT',            'Patent nares, no discharge, septum midline',
     ['Patent nares', 'No discharge', 'Septum midline', 'No polyps',
      'Rhinorrhoea', 'Nasal congestion', 'Septal deviation', 'Epistaxis',
      'Polyps noted', 'Tenderness over sinuses']),

    ('mouth_throat',        'Mouth and Throat',        'HEENT',            'Moist mucous membranes, no erythema, tonsils not enlarged',
     ['Moist mucous membranes', 'Good dentition', 'No erythema', 'Tonsils not enlarged',
      'No exudate', 'Midline uvula', 'Dry mucous membranes', 'Erythema',
      'Tonsillar enlargement', 'Exudate present', 'Oral ulcers', 'Thrush',
      'Poor dentition', 'Macroglossia']),

    # Neck
    ('neck',                'Neck',                    'Neck & Nodes',     'Supple, full ROM, no JVD, no masses, no bruits',
     ['Supple', 'Full ROM', 'No JVD', 'No masses', 'No bruits', 'No tenderness',
      'JVD present', 'Stiffness/nuchal rigidity', 'Lymphadenopathy', 'Bruit present',
      'Trachea midline', 'Tracheal deviation', 'Limited ROM']),

    ('lymph_nodes',         'Lymph Nodes',             'Neck & Nodes',     'No lymphadenopathy',
     ['No lymphadenopathy', 'Cervical nodes not palpable', 'Axillary nodes not palpable',
      'Inguinal nodes not palpable', 'Anterior cervical LAP', 'Posterior cervical LAP',
      'Axillary LAP', 'Inguinal LAP', 'Supraclavicular LAP', 'Generalised LAP',
      'Mobile nodes', 'Fixed nodes', 'Tender nodes', 'Non-tender nodes']),

    # Chest & Respiratory
    ('chest',               'Chest Wall',              'Chest & Lungs',    'Chest wall symmetric, non-tender; no deformity',
     ['Symmetric expansion', 'Non-tender chest wall', 'No deformity', 'Normal AP diameter',
      'Barrel chest', 'Kyphoscoliosis', 'Pectus excavatum', 'Tenderness on palpation',
      'Surgical scars noted', 'Use of accessory muscles']),

    ('lungs',               'Lungs / Respiratory',     'Chest & Lungs',    'CTA bilaterally; no wheeze, crackle, or rub',
     ['Clear to auscultation bilaterally', 'Normal breath sounds', 'No wheezes', 'No crackles',
      'No rhonchi', 'No pleural rub', 'Diminished BS right', 'Diminished BS left',
      'Wheezing', 'Crackles/rales', 'Rhonchi', 'Pleural rub', 'Stridor',
      'Dullness to percussion', 'Hyperresonance', 'Bronchial breath sounds']),

    # Cardiovascular
    ('cardiovascular',      'Cardiovascular',          'Cardiovascular',   'RRR, S1/S2 normal, no murmur, rub, or gallop; pulses 2+ bilaterally',
     ['Regular rate and rhythm', 'S1 S2 normal', 'No murmur', 'No rub', 'No gallop',
      'Pulses 2+ bilaterally', 'Tachycardia', 'Bradycardia', 'Irregular rhythm',
      'Systolic murmur', 'Diastolic murmur', 'S3 gallop', 'S4 gallop', 'Pericardial rub',
      'Peripheral oedema', 'Apex displaced', 'Atrial fibrillation']),

    # Abdomen
    ('abdomen',             'Abdomen',                 'Abdomen',          'Soft, non-tender, non-distended; no organomegaly; BS normoactive',
     ['Soft', 'Non-tender', 'Non-distended', 'Bowel sounds normoactive', 'No organomegaly',
      'No guarding', 'No rebound tenderness', 'Tenderness present', 'Rigid abdomen',
      'Guarding', 'Rebound tenderness', 'Hepatomegaly', 'Splenomegaly',
      'Ascites', 'Bowel sounds absent', 'Bowel sounds hyperactive',
      'Mass palpable', 'Periumbilical tenderness', 'RUQ tenderness', 'RLQ tenderness',
      'LLQ tenderness', 'Epigastric tenderness', 'Flank tenderness', 'Murphys sign +ve']),

    # GU
    ('genitourinary',       'Genitourinary',           'Genitourinary',    'No CVA tenderness; genitalia normal for age',
     ['No CVA tenderness', 'No suprapubic tenderness', 'External genitalia normal',
      'CVA tenderness right', 'CVA tenderness left', 'CVA tenderness bilateral',
      'Suprapubic tenderness', 'Scrotal swelling', 'Hernias present', 'Foley catheter in place',
      'Urethral discharge', 'Genital lesions noted', 'Prostate enlarged', 'Adnexal tenderness']),

    # Musculoskeletal
    ('musculoskeletal',     'Musculoskeletal',         'Musculoskeletal',  'Full ROM all joints; no joint swelling or tenderness; normal muscle tone',
     ['Full ROM', 'No joint swelling', 'No joint tenderness', 'Normal muscle tone', 'Normal muscle strength',
      'Limited ROM', 'Joint swelling', 'Joint tenderness', 'Crepitus', 'Deformity',
      'Muscle weakness', 'Muscle atrophy', 'Contractures', 'Arthritis changes',
      'Effusion present', 'Decreased grip strength']),

    ('spine',               'Spine',                   'Musculoskeletal',  'Spine straight; no tenderness; full flexion/extension',
     ['Spine straight', 'No midline tenderness', 'No paraspinal tenderness', 'Full flexion and extension',
      'No CVAT', 'Kyphosis', 'Scoliosis', 'Lordosis', 'Midline tenderness',
      'Paraspinal muscle spasm', 'Limited flexion', 'Limited extension',
      'Straight leg raise negative', 'Straight leg raise positive']),

    ('extremities',         'Extremities',             'Extremities',      'No oedema, clubbing, or cyanosis; capillary refill < 2 sec',
     ['No oedema', 'No clubbing', 'No cyanosis', 'Capillary refill < 2 sec',
      'Pitting oedema +1', 'Pitting oedema +2', 'Pitting oedema +3', 'Pitting oedema +4',
      'Clubbing', 'Cyanosis', 'Capillary refill > 2 sec', 'Skin changes',
      'Varicose veins', 'Ulceration', 'DVT signs']),

    ('peripheral_pulses',   'Peripheral Pulses',       'Extremities',      'Peripheral pulses 2+ bilaterally; no bruit',
     ['2+ bilaterally', 'Radial 2+ bilaterally', 'Dorsalis pedis 2+ bilaterally',
      'Posterior tibial 2+ bilaterally', 'Femoral 2+ bilaterally',
      'Diminished radial pulse', 'Diminished pedal pulses', 'Absent pedal pulses',
      'Bruit over femoral artery', '1+ weak pulses', 'Pulse deficit']),

    # Neurological
    ('neuro_general',       'Neurological — General',  'Neurological',     'Cranial nerves II–XII intact; motor 5/5; sensory intact; reflexes 2+ symmetric; coordination intact; gait normal',
     ['Cranial nerves intact', 'Motor 5/5 all extremities', 'Sensory intact', 'Reflexes 2+ symmetric',
      'Coordination intact', 'Gait normal', 'Focal neurological deficit', 'Upper motor neuron signs',
      'Lower motor neuron signs', 'Meningism', 'Cerebellar signs']),

    ('cranial_nerves',      'Cranial Nerves',          'Neurological',     'CN II–XII grossly intact',
     ['CN II-XII intact', 'Visual fields full', 'PERRL', 'EOM full', 'Facial sensation intact',
      'Facial symmetry', 'Hearing intact', 'Palate rises symmetrically', 'Tongue midline',
      'CN III palsy', 'CN VI palsy', 'CN VII palsy (facial weakness)', 'CN VIII deficit',
      'Tongue deviation', 'Dysarthria', 'Dysphagia']),

    ('motor',               'Motor Function',          'Neurological',     'Strength 5/5 all extremities; normal tone; no abnormal movements',
     ['Strength 5/5 bilaterally', 'Normal tone', 'No fasciculations', 'No involuntary movements',
      'Strength 4/5 UE', 'Strength 3/5 LE', 'Hemiplegia', 'Hemiparesis', 'Monoplegia',
      'Paraparesis', 'Paraplegia', 'Spasticity', 'Rigidity', 'Hypotonia', 'Hypertonia',
      'Tremor at rest', 'Intention tremor', 'Fasciculations']),

    ('sensory',             'Sensory Function',        'Neurological',     'Sensation intact to light touch and pinprick bilaterally',
     ['Light touch intact', 'Pinprick intact', 'Vibration intact', 'Proprioception intact',
      'Temperature intact', 'Dermatomal sensory loss', 'Glove and stocking pattern loss',
      'Hemisensory loss', 'Hyperesthesia', 'Paresthesia', 'Allodynia']),

    ('reflexes',            'Reflexes',                'Neurological',     'DTRs 2+ bilaterally; plantar responses downgoing',
     ['DTRs 2+ symmetric', 'Plantar responses downgoing', 'Babinski negative',
      'Hyperreflexia', 'Hyporeflexia', 'Areflexia', 'Clonus present', 'Babinski positive',
      'Asymmetric reflexes', 'Hoffman sign positive']),

    ('coordination',        'Coordination',            'Neurological',     'Finger-nose and heel-shin intact; no dysdiadochokinesia',
     ['Finger-nose test normal', 'Heel-shin test normal', 'No dysdiadochokinesia',
      'Rapid alternating movements intact', 'Dysmetria', 'Past-pointing', 'Intention tremor',
      'Dysdiadochokinesia', 'Cerebellar ataxia', 'Romberg negative', 'Romberg positive']),

    ('gait',                'Gait',                    'Neurological',     'Normal gait, steady, no assistive device',
     ['Normal gait', 'Steady', 'No assistive device', 'Ataxic gait', 'Antalgic gait',
      'Spastic gait', 'Steppage gait', 'Waddling gait', 'Parkinsonian gait',
      'Hemiplegic gait', 'Requires assistive device', 'Unable to walk independently']),

    # Psychiatric
    ('psychiatric',         'Psychiatric Examination', 'Psychiatric',      'Appropriate mood/affect, normal thought process, no SI/HI, insight intact',
     ['Appropriate mood', 'Appropriate affect', 'Normal thought process', 'No suicidal ideation',
      'No homicidal ideation', 'Insight intact', 'Judgment intact', 'Cooperative',
      'Anxious', 'Depressed mood', 'Elevated mood/mania', 'Flat/blunted affect',
      'Labile affect', 'Suicidal ideation', 'Homicidal ideation', 'Hallucinations',
      'Delusions', 'Poor insight', 'Poor judgment', 'Poor impulse control',
      'Agitated/aggressive', 'Psychomotor retardation']),
]

# Mapping: system_key → (label, group, quick_normal, quick_findings)
EXAM_SYSTEM_MAP = {k: (label, group, qn, qf) for k, label, group, qn, qf in EXAM_SYSTEMS}

# Status choices
STATUS_CHOICES = [
    ('normal',       'Normal'),
    ('mild',         'Mild Abnormality'),
    ('moderate',     'Moderate Abnormality'),
    ('severe',       'Severe Abnormality'),
    ('not_examined', 'Not Examined'),
]

STATUS_COLORS = {
    'normal':       'green',
    'mild':         'yellow',
    'moderate':     'orange',
    'severe':       'red',
    'not_examined': 'slate',
}


def _get_visit(visit_id):
    from django.shortcuts import get_object_or_404
    return get_object_or_404(
        Visit.objects.select_related('patient', 'doctor', 'department'),
        pk=visit_id,
    )


def _exam_context(exam):
    """Build JS-ready findings dict for template rendering."""
    return {k: exam.findings.get(k, {'status': '', 'comment': ''}) for k, *_ in EXAM_SYSTEMS}


# ── Create ────────────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def physical_exam_create(request, visit_id):
    visit = _get_visit(visit_id)
    latest_vitals = visit.vital_signs.order_by('-recorded_at').first()
    templates = PhysicalExamTemplate.objects.filter(is_active=True).order_by('specialty', 'name')

    if request.method == 'POST':
        findings = {}
        for key, *_ in EXAM_SYSTEMS:
            status  = request.POST.get(f'{key}_status', '').strip()
            comment = request.POST.get(f'{key}_comment', '').strip()
            if status or comment:
                findings[key] = {'status': status, 'comment': comment}

        exam = PhysicalExamination.objects.create(
            visit=visit,
            examiner=request.user,
            template_id=request.POST.get('template') or None,
            findings=findings,
            overall_summary=request.POST.get('overall_summary', '').strip(),
            is_complete=bool(request.POST.get('is_complete')),
        )
        log_action(
            request.user, AuditLog.Action.CREATE, AuditLog.Module.DOCTOR,
            object_type='PhysicalExamination', object_id=exam.pk,
            object_repr=f'{visit.patient.full_name} — PE {exam.pk}',
            description='Physical examination recorded',
            request=request,
        )
        messages.success(request, 'Physical examination saved.')
        return redirect('physical_exam_detail', visit_id=visit.pk, exam_id=exam.pk)

    context = {
        'visit': visit,
        'latest_vitals': latest_vitals,
        'templates': templates,
        'exam_systems': EXAM_SYSTEMS,
        'status_choices': STATUS_CHOICES,
        'exam_systems_json': json.dumps([
            {'key': k, 'label': label, 'group': group, 'quick_normal': qn, 'findings': qf}
            for k, label, group, qn, qf in EXAM_SYSTEMS
        ]),
        'is_edit': False,
    }
    return render(request, 'physical_exam/form.html', context)


# ── Edit ──────────────────────────────────────────────────────────────────────

@hms_permission_required('core.write_clinical_note')
def physical_exam_edit(request, visit_id, exam_id):
    visit = _get_visit(visit_id)
    exam  = get_object_or_404(PhysicalExamination, pk=exam_id, visit=visit)
    latest_vitals = visit.vital_signs.order_by('-recorded_at').first()
    templates = PhysicalExamTemplate.objects.filter(is_active=True).order_by('specialty', 'name')

    if request.method == 'POST':
        findings = {}
        for key, *_ in EXAM_SYSTEMS:
            status  = request.POST.get(f'{key}_status', '').strip()
            comment = request.POST.get(f'{key}_comment', '').strip()
            if status or comment:
                findings[key] = {'status': status, 'comment': comment}

        exam.findings        = findings
        exam.overall_summary = request.POST.get('overall_summary', '').strip()
        exam.is_complete     = bool(request.POST.get('is_complete'))
        exam.template_id     = request.POST.get('template') or None
        exam.save()

        log_action(
            request.user, AuditLog.Action.UPDATE, AuditLog.Module.DOCTOR,
            object_type='PhysicalExamination', object_id=exam.pk,
            object_repr=f'{visit.patient.full_name} — PE {exam.pk}',
            description='Physical examination updated',
            request=request,
        )
        messages.success(request, 'Physical examination updated.')
        return redirect('physical_exam_detail', visit_id=visit.pk, exam_id=exam.pk)

    context = {
        'visit': visit,
        'exam': exam,
        'latest_vitals': latest_vitals,
        'templates': templates,
        'exam_systems': EXAM_SYSTEMS,
        'status_choices': STATUS_CHOICES,
        'existing_findings': exam.findings,
        'exam_systems_json': json.dumps([
            {'key': k, 'label': label, 'group': group, 'quick_normal': qn, 'findings': qf}
            for k, label, group, qn, qf in EXAM_SYSTEMS
        ]),
        'is_edit': True,
    }
    return render(request, 'physical_exam/form.html', context)


# ── Auto-save (AJAX) ──────────────────────────────────────────────────────────

@require_POST
@hms_permission_required('core.write_clinical_note')
def physical_exam_autosave(request, visit_id, exam_id):
    visit = _get_visit(visit_id)
    exam  = get_object_or_404(PhysicalExamination, pk=exam_id, visit=visit)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    findings = {}
    for key, *_ in EXAM_SYSTEMS:
        entry = payload.get(key)
        if entry and isinstance(entry, dict):
            findings[key] = {
                'status':  str(entry.get('status',  '')).strip(),
                'comment': str(entry.get('comment', '')).strip(),
            }

    exam.findings        = findings
    exam.overall_summary = str(payload.get('overall_summary', '')).strip()
    exam.save(update_fields=['findings', 'overall_summary', 'updated_at'])

    return JsonResponse({'ok': True, 'saved_at': exam.updated_at.strftime('%H:%M:%S')})


# ── Detail (read-only) ────────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_note')
def physical_exam_detail(request, visit_id, exam_id):
    visit = _get_visit(visit_id)
    exam  = get_object_or_404(
        PhysicalExamination.objects.select_related('examiner', 'template'),
        pk=exam_id, visit=visit,
    )

    status_labels = dict(STATUS_CHOICES)

    grouped = {}
    for key, label, group, qn, _ in EXAM_SYSTEMS:
        finding = exam.findings.get(key, {})
        status  = finding.get('status', '')
        if group not in grouped:
            grouped[group] = []
        grouped[group].append({
            'key':            key,
            'label':          label,
            'status':         status,
            'status_display': status_labels.get(status, ''),
            'comment':        finding.get('comment', ''),
        })

    context = {
        'visit': visit,
        'exam': exam,
        'grouped': grouped,
        'status_colors': STATUS_COLORS,
    }
    return render(request, 'physical_exam/detail.html', context)


# ── Print ─────────────────────────────────────────────────────────────────────

@hms_permission_required('core.read_clinical_note')
def physical_exam_print(request, visit_id, exam_id):
    visit = _get_visit(visit_id)
    exam  = get_object_or_404(
        PhysicalExamination.objects.select_related('examiner', 'template'),
        pk=exam_id, visit=visit,
    )

    status_labels = dict(STATUS_CHOICES)
    grouped = {}
    for key, label, group, qn, _ in EXAM_SYSTEMS:
        finding = exam.findings.get(key, {})
        status  = finding.get('status', '')
        if group not in grouped:
            grouped[group] = []
        grouped[group].append({
            'key':            key,
            'label':          label,
            'status':         status,
            'status_display': status_labels.get(status, ''),
            'comment':        finding.get('comment', ''),
        })

    context = {
        'visit': visit,
        'exam': exam,
        'grouped': grouped,
        'status_labels': dict(STATUS_CHOICES),
    }
    return render(request, 'physical_exam/print.html', context)


# ── Template management (admin) ───────────────────────────────────────────────

@hms_permission_required('core.manage_exam_templates')
def exam_template_list(request):
    templates = PhysicalExamTemplate.objects.select_related('created_by').all()
    return render(request, 'physical_exam/template_list.html', {'templates': templates})


@hms_permission_required('core.manage_exam_templates')
def exam_template_create(request):
    specialties = PhysicalExamTemplate.Specialty.choices
    if request.method == 'POST':
        tpl = PhysicalExamTemplate.objects.create(
            name=request.POST.get('name', '').strip(),
            specialty=request.POST.get('specialty', PhysicalExamTemplate.Specialty.GENERAL),
            description=request.POST.get('description', '').strip(),
            systems_config=_parse_template_config(request),
            is_active=bool(request.POST.get('is_active')),
            is_default=bool(request.POST.get('is_default')),
            created_by=request.user,
        )
        messages.success(request, f'Template "{tpl.name}" created.')
        return redirect('exam_template_list')
    return render(request, 'physical_exam/template_form.html', {
        'specialties': specialties,
        'exam_systems': EXAM_SYSTEMS,
        'status_choices': STATUS_CHOICES,
        'is_edit': False,
    })


@hms_permission_required('core.manage_exam_templates')
def exam_template_edit(request, pk):
    tpl = get_object_or_404(PhysicalExamTemplate, pk=pk)
    specialties = PhysicalExamTemplate.Specialty.choices
    if request.method == 'POST':
        tpl.name           = request.POST.get('name', '').strip()
        tpl.specialty      = request.POST.get('specialty', tpl.specialty)
        tpl.description    = request.POST.get('description', '').strip()
        tpl.systems_config = _parse_template_config(request)
        tpl.is_active      = bool(request.POST.get('is_active'))
        tpl.is_default     = bool(request.POST.get('is_default'))
        tpl.save()
        messages.success(request, f'Template "{tpl.name}" updated.')
        return redirect('exam_template_list')

    config_map = {item['key']: item for item in tpl.systems_config}
    return render(request, 'physical_exam/template_form.html', {
        'tpl': tpl,
        'specialties': specialties,
        'exam_systems': EXAM_SYSTEMS,
        'status_choices': STATUS_CHOICES,
        'config_map_json': json.dumps(config_map),
        'is_edit': True,
    })


def _parse_template_config(request):
    config = []
    for key, *_ in EXAM_SYSTEMS:
        config.append({
            'key':            key,
            'include':        bool(request.POST.get(f'sys_{key}_include')),
            'default_status': request.POST.get(f'sys_{key}_default', 'normal'),
        })
    return config
