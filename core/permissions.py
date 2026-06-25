"""
HMS Role and Permission Definitions.

Roles map 1:1 to Django auth Groups.  Every permission string uses the
format  'app_label.codename'  so it can be passed directly to
`user.has_perm()` or used in template `{% if perms.core.codename %}`.
"""

# ── Role names (must match the Group names created by setup_rbac) ──────────
ROLE_NAMES = [
    'Nurse',
    'Doctor',
    'Receptionist',
    'Laboratory Staff',
    'Anesthesia Team',
    'Radiologist',
    'Finance Staff',
    'Cashier',
    'HR Staff',
    'Administrator',
    'Pharmacy Admin',
    'Pharmacy Sales',
    'Store Staff',
    'Medical Director',
    'Triage Staff',
]

# ── Role → permission set mapping ──────────────────────────────────────────
# Mixes Django auto-generated model permissions (core.add_patient, etc.)
# and custom HMS permissions defined in HMSPermissions.Meta.permissions.
ROLE_PERMISSIONS = {
    'Nurse': {
        'core.view_patient',
        'core.view_visit',
        'core.view_queue', 'core.change_queue',
        'core.manage_queue',
        'core.write_nursing_note', 'core.read_nursing_note',
        'core.read_clinical_note',
        'core.read_prescription',
        'core.perform_triage',
        'core.record_vital_signs', 'core.read_vital_signs',
        'core.write_care_plan', 'core.read_care_plan',
    },
    'Doctor': {
        'core.view_patient', 'core.change_patient',
        'core.add_visit', 'core.view_visit', 'core.change_visit',
        'core.write_clinical_note', 'core.read_clinical_note',
        'core.write_nursing_note', 'core.read_nursing_note',
        'core.write_prescription', 'core.read_prescription',
        'core.write_diagnosis', 'core.read_diagnosis',
        'core.request_lab_test', 'core.read_lab_request', 'core.read_lab_result',
        'core.request_imaging', 'core.read_imaging_request', 'core.read_imaging_report',
        'core.read_clinical_reports',
        'core.record_vital_signs', 'core.read_vital_signs',
        'core.write_care_plan', 'core.read_care_plan',
        'core.read_appointment', 'core.manage_appointments',
    },
    'Receptionist': {
        'core.add_patient', 'core.view_patient', 'core.change_patient',
        'core.add_visit', 'core.view_visit',
        'core.view_queue', 'core.manage_queue',
        'core.read_appointment', 'core.manage_appointments',
        'core.read_billing',
    },
    'Laboratory Staff': {
        'core.view_patient',
        'core.read_lab_request', 'core.process_lab_test',
        'core.write_lab_result', 'core.read_lab_result',
    },
    'Anesthesia Team': {
        'core.view_patient', 'core.view_visit',
        'core.read_clinical_note', 'core.read_prescription',
        'core.read_vital_signs',
        'core.write_anesthesia_record', 'core.read_anesthesia_record',
    },
    'Radiologist': {
        'core.view_patient',
        'core.read_imaging_request',
        'core.process_imaging',
        'core.write_imaging_report', 'core.read_imaging_report',
    },
    'Finance Staff': {
        'core.view_patient', 'core.view_visit',
        'core.read_billing', 'core.create_invoice', 'core.manage_billing',
        'core.process_payment',
        'core.read_financial_report',
    },
    'Cashier': {
        'core.view_patient', 'core.view_visit',
        'core.read_billing', 'core.create_invoice',
        'core.process_payment',
    },
    'HR Staff': {
        'core.read_employee', 'core.manage_employees',
        'core.manage_attendance',
        'core.manage_payroll',
    },
    'Administrator': {
        'core.add_patient', 'core.view_patient', 'core.change_patient', 'core.delete_patient',
        'core.add_visit', 'core.view_visit', 'core.change_visit', 'core.delete_visit',
        'core.view_queue', 'core.change_queue', 'core.manage_queue',
        'core.manage_users', 'core.manage_roles',
        'core.system_configuration', 'core.read_audit_log',
        'core.manage_departments',
        'core.read_clinical_reports', 'core.read_financial_report',
        'core.read_department_reports',
        'core.read_employee', 'core.read_billing',
    },
    'Pharmacy Admin': {
        'core.view_patient',
        'core.read_prescription', 'core.read_prescription_for_dispensing',
        'core.read_medication_inventory', 'core.manage_medication',
        'core.dispense_medication',
    },
    'Pharmacy Sales': {
        'core.read_prescription_for_dispensing',
        'core.dispense_medication',
        'core.read_medication_inventory',
        'core.process_pharmacy_sale',
    },
    'Store Staff': {
        'core.read_inventory', 'core.manage_inventory',
        'core.create_purchase_order',
        'core.approve_purchase_order',
    },
    'Medical Director': {
        'core.view_patient', 'core.change_patient',
        'core.add_visit', 'core.view_visit', 'core.change_visit',
        'core.view_queue', 'core.manage_queue',
        'core.write_clinical_note', 'core.read_clinical_note',
        'core.write_prescription', 'core.read_prescription',
        'core.write_diagnosis', 'core.read_diagnosis',
        'core.request_lab_test', 'core.read_lab_request', 'core.read_lab_result',
        'core.request_imaging', 'core.read_imaging_request', 'core.read_imaging_report',
        'core.read_clinical_reports',
        'core.manage_departments', 'core.read_department_reports',
        'core.read_employee',
        'core.read_financial_report',
        'core.read_appointment', 'core.manage_appointments',
    },
    'Triage Staff': {
        'core.view_patient',
        'core.perform_triage', 'core.manage_queue',
        'core.view_queue', 'core.change_queue',
        'core.write_nursing_note', 'core.read_nursing_note',
        'core.view_visit',
        'core.record_vital_signs', 'core.read_vital_signs',
    },
}

# ── Dashboard module catalogue ──────────────────────────────────────────────
# Each entry describes one module card on the dashboard.
# `permission`  — the perm checked to decide if the user sees this card.
# `available`   — False means "coming soon"; True means the URL is live.
DASHBOARD_MODULES = [
    {
        'id': 'patient_registration',
        'title': 'Patient Registration',
        'description': 'Register new patients and manage medical records',
        'url_name': 'register_patient',
        'permission': 'core.add_patient',
        'color': 'blue',
        'available': True,
        'icon_path': (
            'M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0z'
            'M3 20a6 6 0 0112 0v1H3v-1z'
        ),
    },
    {
        'id': 'patient_search',
        'title': 'Patient Search',
        'description': 'Search and view patient records',
        'url_name': 'patient_search',
        'permission': 'core.view_patient',
        'color': 'emerald',
        'available': True,
        'icon_path': 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
    },
    {
        'id': 'triage',
        'title': 'Triage Assessment',
        'description': 'Emergency triage and patient prioritization',
        'url_name': None,
        'permission': 'core.perform_triage',
        'color': 'red',
        'available': False,
        'icon_path': (
            'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 '
            '1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 '
            '16c-.77 1.333.192 3 1.732 3z'
        ),
    },
    {
        'id': 'queue_management',
        'title': 'Queue Management',
        'description': 'Manage patient queues and daily workflow',
        'url_name': None,
        'permission': 'core.manage_queue',
        'color': 'purple',
        'available': False,
        'icon_path': (
            'M4 6h16M4 10h16M4 14h16M4 18h16'
        ),
    },
    {
        'id': 'clinical_notes',
        'title': 'Clinical Notes',
        'description': 'Doctor and nursing clinical documentation',
        'url_name': None,
        'permission': 'core.write_clinical_note',
        'color': 'indigo',
        'available': False,
        'icon_path': (
            'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 '
            '1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'
        ),
    },
    {
        'id': 'prescriptions',
        'title': 'Prescriptions',
        'description': 'Write and manage medication prescriptions',
        'url_name': None,
        'permission': 'core.write_prescription',
        'color': 'teal',
        'available': False,
        'icon_path': (
            'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 '
            '00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 '
            '2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01'
        ),
    },
    {
        'id': 'laboratory',
        'title': 'Laboratory',
        'description': 'Lab test requests and results management',
        'url_name': None,
        'permission': 'core.read_lab_request',
        'color': 'yellow',
        'available': False,
        'icon_path': (
            'M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 '
            '00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 '
            '15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 '
            '00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 '
            '3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 '
            '2 0 009 10.172V5L8 4z'
        ),
    },
    {
        'id': 'radiology',
        'title': 'Radiology / Imaging',
        'description': 'Imaging requests and radiology reports',
        'url_name': None,
        'permission': 'core.read_imaging_request',
        'color': 'cyan',
        'available': False,
        'icon_path': (
            'M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 '
            '0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18'
        ),
    },
    {
        'id': 'anesthesia',
        'title': 'Anesthesia',
        'description': 'Anesthesia records and surgical preparation',
        'url_name': None,
        'permission': 'core.read_anesthesia_record',
        'color': 'slate',
        'available': False,
        'icon_path': (
            'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 '
            '4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z'
        ),
    },
    {
        'id': 'pharmacy',
        'title': 'Pharmacy',
        'description': 'Medication dispensing and inventory',
        'url_name': None,
        'permission': 'core.read_medication_inventory',
        'color': 'green',
        'available': False,
        'icon_path': (
            'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 '
            '2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 '
            '012-2h6a2 2 0 012 2v2M7 7h10'
        ),
    },
    {
        'id': 'billing',
        'title': 'Billing & Finance',
        'description': 'Invoicing, payments, and financial records',
        'url_name': None,
        'permission': 'core.read_billing',
        'color': 'orange',
        'available': False,
        'icon_path': (
            'M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 '
            '2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 '
            '2 0 11-4 0 2 2 0 014 0z'
        ),
    },
    {
        'id': 'inventory',
        'title': 'Store & Inventory',
        'description': 'Stock management and procurement',
        'url_name': None,
        'permission': 'core.read_inventory',
        'color': 'amber',
        'available': False,
        'icon_path': (
            'M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 '
            '2h10a2 2 0 002-2V8m-9 4h4'
        ),
    },
    {
        'id': 'hr',
        'title': 'Human Resources',
        'description': 'Employee management and HR operations',
        'url_name': None,
        'permission': 'core.read_employee',
        'color': 'violet',
        'available': False,
        'icon_path': (
            'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-'
            '1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.'
            '126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 '
            '11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 '
            '11-4 0 2 2 0 014 0z'
        ),
    },
    {
        'id': 'reports',
        'title': 'Reports & Analytics',
        'description': 'Clinical, financial, and operational reports',
        'url_name': None,
        'permission': 'core.read_clinical_reports',
        'color': 'rose',
        'available': False,
        'icon_path': (
            'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 '
            '002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 '
            '2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 '
            '2 0 01-2-2z'
        ),
    },
    {
        'id': 'user_management',
        'title': 'User Management',
        'description': 'Manage staff accounts and role assignments',
        'url_name': 'user_list',
        'permission': 'core.manage_users',
        'color': 'gray',
        'available': True,
        'icon_path': (
            'M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 '
            '6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z'
        ),
    },
]

# ── Role display metadata (badge colour + description) ─────────────────────
ROLE_META = {
    'Nurse':            {'badge': 'bg-pink-100 text-pink-800',    'desc': 'Patient care & monitoring'},
    'Doctor':           {'badge': 'bg-blue-100 text-blue-800',    'desc': 'Clinical diagnosis & treatment'},
    'Receptionist':     {'badge': 'bg-sky-100 text-sky-800',      'desc': 'Patient registration & appointments'},
    'Laboratory Staff': {'badge': 'bg-yellow-100 text-yellow-800','desc': 'Lab tests & results'},
    'Anesthesia Team':  {'badge': 'bg-slate-100 text-slate-700',  'desc': 'Anesthesia & surgical prep'},
    'Radiologist':      {'badge': 'bg-cyan-100 text-cyan-800',    'desc': 'Imaging studies & reports'},
    'Finance Staff':    {'badge': 'bg-orange-100 text-orange-800','desc': 'Billing & financial management'},
    'Cashier':          {'badge': 'bg-amber-100 text-amber-800',  'desc': 'Payment processing'},
    'HR Staff':         {'badge': 'bg-violet-100 text-violet-800','desc': 'Employee & HR management'},
    'Administrator':    {'badge': 'bg-red-100 text-red-800',      'desc': 'System administration'},
    'Pharmacy Admin':   {'badge': 'bg-green-100 text-green-800',  'desc': 'Pharmacy & medication management'},
    'Pharmacy Sales':   {'badge': 'bg-teal-100 text-teal-800',    'desc': 'Medication dispensing & sales'},
    'Store Staff':      {'badge': 'bg-lime-100 text-lime-800',    'desc': 'Inventory & store management'},
    'Medical Director': {'badge': 'bg-indigo-100 text-indigo-800','desc': 'Clinical oversight & direction'},
    'Triage Staff':     {'badge': 'bg-rose-100 text-rose-800',    'desc': 'Emergency triage & assessment'},
}
