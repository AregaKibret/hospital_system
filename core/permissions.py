"""
HMS Role and Permission Definitions.

Roles map 1:1 to Django auth Groups.  Every permission string uses the
format  'app_label.codename'  so it can be passed directly to
`user.has_perm()` or used in template `{% if perms.core.codename %}`.
"""

# ── Role names (must match the Group names created by setup_rbac) ──────────
ROLE_NAMES = [
    # Administration
    'Administrator',
    'Medical Director',

    # Reception
    'Receptionist',

    # Medical Records
    'Medical Records Officer',

    # Medical / Clinical
    'Doctor',
    'Ward Doctor',
    'Emergency Doctor',
    'Surgeon',

    # Nursing
    'Nurse',
    'Ward Nurse',
    'OR Nurse',
    'Emergency Nurse',
    'Triage Staff',
    'Ward Supervisor',

    # Laboratory
    'Laboratory Staff',
    'Lab Supervisor',
    'Lab Manager',

    # Radiology
    'Radiologist',
    'Radiology Technician',

    # Anesthesia / OR
    'Anesthesia Team',

    # Pharmacy
    'Pharmacy Admin',
    'Pharmacist',
    'Pharmacy Sales',
    'Pharmacy Manager',

    # Store / Inventory
    'Store Manager',
    'Store Staff',
    'Store Officer',
    'Asset Manager',

    # Finance
    'Finance Manager',
    'Finance Staff',
    'Cashier',

    # HR
    'HR Manager',
    'HR Staff',
    'HR Officer',

    # Department Medication Stores
    'Dept Medication Manager',
    'Ward Store User',
    'Emergency Store Staff',
]

# ── Shared permission sets (building blocks) ─────────────────────────────────
_CLINICAL_BASE = {
    'core.view_patient',
    'core.view_visit',
    'core.read_clinical_note',
    'core.read_prescription',
    'core.read_diagnosis',
    'core.read_vital_signs',
    'core.read_lab_result',
    'core.read_imaging_report',
}

_DOCTOR_WRITE = {
    'core.change_patient',
    'core.add_visit', 'core.change_visit',
    'core.write_clinical_note',
    'core.write_nursing_note', 'core.read_nursing_note',
    'core.write_prescription',
    'core.write_diagnosis',
    'core.request_lab_test', 'core.read_lab_request',
    'core.request_imaging', 'core.read_imaging_request',
    'core.read_clinical_reports',
    'core.record_vital_signs',
    'core.write_care_plan', 'core.read_care_plan',
}

_NURSE_BASE = {
    'core.view_patient', 'core.view_visit',
    'core.view_queue', 'core.change_queue', 'core.manage_queue',
    'core.write_nursing_note', 'core.read_nursing_note',
    'core.read_clinical_note',
    'core.read_prescription',
    'core.record_vital_signs', 'core.read_vital_signs',
    'core.write_care_plan', 'core.read_care_plan',
}

_DEPT_PHARM_USER = {
    'core.view_dept_inventory',
    'core.request_medication_transfer',
    'core.record_dept_usage',
    'core.view_dept_reports',
}

_DEPT_PHARM_MANAGER = _DEPT_PHARM_USER | {
    'core.manage_dept_stores',
    'core.approve_medication_transfer',
}

_PATIENT_FLOW_READ = {
    'core.view_patient_flow',
    'core.view_dept_worklist',
}

_PATIENT_FLOW_MANAGE = _PATIENT_FLOW_READ | {
    'core.manage_patient_flow',
}

_SURGERY_READ = {
    'core.read_surgery',
    'core.read_postop_note',
    'core.read_periop_document_history',
}

_SURGERY_DOCTOR = _SURGERY_READ | {
    'core.order_surgery',
    'core.write_operative_note',
    'core.read_surgery_reports',
}

_SURGERY_BOOKING = {
    'core.counsel_surgery_patient',
    'core.process_surgery_booking_deposit',
    'core.initiate_surgery_admission',
    'core.collect_surgery_pre_deposit',
    'core.settle_surgery_account',
}

_SURGERY_FULL = _SURGERY_DOCTOR | _SURGERY_BOOKING | {
    'core.approve_surgery_order',
    'core.manage_or_schedule',
    'core.write_surgery_anesthesia',
    'core.write_postop_note',
    'core.manage_surgery_consumables',
    'core.generate_surgery_billing',
    'core.manage_procedure_master',
}

_FACILITY_CLINICAL = {
    'core.view_facility',
    'core.assign_bed',
}

_FACILITY_ADMIN = _FACILITY_CLINICAL | {
    'core.manage_facilities',
    'core.view_facility_reports',
}

_ADMISSION_REQUESTER = {'core.request_admission'}

_ADMISSION_ADMIN = {
    'core.request_admission', 'core.review_admission_request',
    'core.view_admission_dashboard', 'core.view_admission_reports', 'core.manage_deposit_rules',
}

_ATTACHMENT_BASE = {'core.upload_attachment', 'core.view_attachments'}
_ATTACHMENT_CLINICAL = _ATTACHMENT_BASE | {'core.view_confidential_attachments', 'core.replace_attachment'}
_ATTACHMENT_ADMIN = _ATTACHMENT_CLINICAL | {
    'core.delete_attachment', 'core.restore_attachment',
    'core.manage_attachment_categories', 'core.view_attachment_reports',
}

_CERTIFICATE_READ = {'core.read_medical_certificate', 'core.read_death_certificate'}
_CERTIFICATE_DOCTOR = _CERTIFICATE_READ | {
    'core.write_medical_certificate', 'core.finalize_medical_certificate',
    'core.write_death_certificate', 'core.finalize_death_certificate',
}
_CERTIFICATE_VOID = {'core.void_medical_certificate', 'core.void_death_certificate'}

_EXAM_TEMPLATES = {'core.manage_exam_templates'}


# ── Role → permission set mapping ──────────────────────────────────────────
ROLE_PERMISSIONS = {
    # ── Administration ──────────────────────────────────────────────────────
    'Administrator': {
        'core.add_patient', 'core.view_patient', 'core.change_patient', 'core.delete_patient',
        'core.add_visit', 'core.view_visit', 'core.change_visit', 'core.delete_visit',
        'core.view_queue', 'core.change_queue', 'core.manage_queue',
        'core.manage_users', 'core.manage_roles',
        'core.system_configuration', 'core.manage_system_config', 'core.read_audit_log',
        'core.manage_departments',
        'core.read_clinical_reports', 'core.read_financial_report',
        'core.read_department_reports',
        'core.read_employee', 'core.read_billing',
        *_SURGERY_FULL,
        *_FACILITY_ADMIN,
        *_ADMISSION_ADMIN,
        *_ATTACHMENT_ADMIN,
        *_PATIENT_FLOW_MANAGE,
        'core.manage_discharge',
        'core.manage_card_types', 'core.override_card_expiry', 'core.view_card_reports',
        'core.manage_specializations', 'core.manage_doctors', 'core.view_specialization_reports',
        'core.read_inventory', 'core.manage_inventory', 'core.perform_stock_count',
        'core.approve_stock_count', 'core.manage_inventory_periods',
        'core.view_store_reports', 'core.export_store_reports',
        'core.manage_employee_signatures', 'core.delete_employee_signature',
        'core.manage_lab_services', 'core.view_lab_reports',
        'core.ward_supervisor_approve', 'core.view_nursing_dashboard',
        'core.view_dept_reports',
        *_CERTIFICATE_DOCTOR, *_CERTIFICATE_VOID,
        *_EXAM_TEMPLATES,
    },
    'Medical Director': {
        *_CLINICAL_BASE, *_DOCTOR_WRITE,
        'core.manage_departments', 'core.read_department_reports',
        'core.read_employee',
        'core.read_financial_report',
        'core.read_appointment', 'core.manage_appointments',
        'core.read_audit_log',
        *_SURGERY_FULL,
        *_FACILITY_ADMIN,
        *_ADMISSION_ADMIN,
        *_ATTACHMENT_CLINICAL, 'core.view_attachment_reports',
        *_PATIENT_FLOW_MANAGE,
        'core.manage_discharge',
        'core.manage_card_types', 'core.override_card_expiry', 'core.view_card_reports',
        'core.manage_specializations', 'core.manage_doctors', 'core.view_specialization_reports',
        *_CERTIFICATE_DOCTOR, *_CERTIFICATE_VOID,
    },

    # ── Reception ───────────────────────────────────────────────────────────
    'Receptionist': {
        'core.add_patient', 'core.view_patient', 'core.change_patient',
        'core.add_visit', 'core.view_visit',
        'core.view_queue', 'core.manage_queue',
        'core.read_appointment', 'core.manage_appointments',
        'core.manage_doctor_availability',
        'core.view_appointment_reports', 'core.export_appointment_reports',
        'core.read_billing',
        *_PATIENT_FLOW_READ,
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER,
        'core.view_admission_dashboard',
        *_ATTACHMENT_BASE,
        'core.read_surgery',
        'core.counsel_surgery_patient',
        'core.initiate_surgery_admission',
        'core.collect_surgery_pre_deposit',
    },

    # ── Medical Records ─────────────────────────────────────────────────────
    'Medical Records Officer': {
        'core.view_patient', 'core.view_visit',
        *_ATTACHMENT_CLINICAL,
        'core.view_attachment_reports',
        *_CERTIFICATE_READ,
    },

    # ── Medical / Clinical Doctors ──────────────────────────────────────────
    'Doctor': {
        *_CLINICAL_BASE, *_DOCTOR_WRITE,
        'core.read_appointment', 'core.manage_appointments',
        'core.view_appointment_reports',
        *_SURGERY_DOCTOR,
        'core.read_surgery', 'core.order_surgery',
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER, 'core.view_admission_dashboard',
        *_ATTACHMENT_CLINICAL,
        *_PATIENT_FLOW_MANAGE,
        'core.manage_discharge',
        *_CERTIFICATE_DOCTOR,
    },
    'Ward Doctor': {
        *_CLINICAL_BASE, *_DOCTOR_WRITE,
        'core.read_appointment', 'core.manage_appointments',
        *_SURGERY_DOCTOR,
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER, 'core.view_admission_dashboard',
        *_ATTACHMENT_CLINICAL,
        *_PATIENT_FLOW_MANAGE,
        'core.manage_discharge',
        *_CERTIFICATE_DOCTOR,
    },
    'Emergency Doctor': {
        *_CLINICAL_BASE, *_DOCTOR_WRITE,
        'core.perform_triage',
        'core.view_dept_inventory', 'core.view_dept_reports',
        'core.read_appointment', 'core.manage_appointments',
        *_SURGERY_DOCTOR,
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER, 'core.view_admission_dashboard',
        *_ATTACHMENT_CLINICAL,
        *_PATIENT_FLOW_MANAGE,
        'core.manage_discharge',
        *_CERTIFICATE_DOCTOR,
    },
    'Surgeon': {
        *_CLINICAL_BASE, *_DOCTOR_WRITE,
        'core.write_anesthesia_record', 'core.read_anesthesia_record',
        'core.view_dept_inventory', 'core.view_dept_reports',
        *_SURGERY_FULL,
        'core.read_surgery', 'core.order_surgery',
        'core.approve_surgery_order', 'core.manage_or_schedule',
        'core.read_appointment', 'core.manage_appointments', 'core.view_appointment_reports',
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER, 'core.view_admission_dashboard',
        *_ATTACHMENT_CLINICAL,
        *_PATIENT_FLOW_MANAGE,
        'core.manage_discharge',
        *_CERTIFICATE_DOCTOR,
    },

    # ── Nursing ─────────────────────────────────────────────────────────────
    'Nurse': {
        *_NURSE_BASE,
        'core.perform_triage',
        *_DEPT_PHARM_USER,
        *_PATIENT_FLOW_READ,
        'core.read_surgery', 'core.read_postop_note', 'core.add_periop_nursing_note',
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER,
        *_ATTACHMENT_BASE,
        'core.view_nursing_dashboard',
        *_CERTIFICATE_READ,
    },
    'Ward Nurse': {
        *_NURSE_BASE,
        *_DEPT_PHARM_USER,
        *_PATIENT_FLOW_READ,
        'core.read_surgery', 'core.read_postop_note', 'core.add_periop_nursing_note',
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER,
        *_ATTACHMENT_BASE,
        'core.view_nursing_dashboard',
        *_CERTIFICATE_READ,
    },
    'OR Nurse': {
        *_NURSE_BASE,
        *_DEPT_PHARM_USER,
        'core.read_anesthesia_record',
        *_SURGERY_READ,
        'core.manage_or_schedule',
        'core.manage_surgery_consumables',
        'core.add_periop_nursing_note',
        *_FACILITY_CLINICAL,
        *_ATTACHMENT_BASE,
        'core.view_nursing_dashboard',
    },
    'Emergency Nurse': {
        *_NURSE_BASE,
        'core.perform_triage',
        *_DEPT_PHARM_USER,
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER,
        *_ATTACHMENT_BASE,
        'core.view_nursing_dashboard',
        *_CERTIFICATE_READ,
    },
    'Triage Staff': {
        'core.view_patient',
        'core.perform_triage', 'core.manage_queue',
        'core.view_queue', 'core.change_queue',
        'core.write_nursing_note', 'core.read_nursing_note',
        'core.view_visit',
        'core.record_vital_signs', 'core.read_vital_signs',
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER,
    },
    'Ward Supervisor': {
        *_NURSE_BASE,
        *_DEPT_PHARM_MANAGER,
        'core.ward_supervisor_approve',
        *_PATIENT_FLOW_READ,
        *_FACILITY_CLINICAL,
        *_ADMISSION_REQUESTER,
        *_ATTACHMENT_BASE,
        'core.view_nursing_dashboard',
    },

    # ── Laboratory ──────────────────────────────────────────────────────────
    'Laboratory Staff': {
        'core.view_patient',
        'core.read_lab_request', 'core.process_lab_test',
        'core.write_lab_result', 'core.read_lab_result',
        'core.collect_lab_sample',
        *_ATTACHMENT_BASE,
    },
    'Lab Supervisor': {
        'core.view_patient',
        'core.read_lab_request', 'core.process_lab_test',
        'core.write_lab_result', 'core.read_lab_result',
        'core.collect_lab_sample', 'core.release_lab_result',
        'core.manage_lab_services', 'core.view_lab_reports',
        'core.read_clinical_reports',
        'core.manage_departments',
        *_ATTACHMENT_BASE,
    },
    'Lab Manager': {
        'core.view_patient',
        'core.read_lab_request', 'core.process_lab_test',
        'core.write_lab_result', 'core.read_lab_result',
        'core.collect_lab_sample', 'core.release_lab_result',
        'core.manage_lab_services', 'core.view_lab_reports',
        'core.read_clinical_reports',
        'core.read_financial_report',
        'core.manage_departments',
        *_ATTACHMENT_BASE,
    },

    # ── Radiology ───────────────────────────────────────────────────────────
    'Radiologist': {
        'core.view_patient',
        'core.read_imaging_request',
        'core.process_imaging',
        'core.write_imaging_report', 'core.read_imaging_report',
        'core.manage_imaging_services',
        *_ATTACHMENT_BASE,
    },
    'Radiology Technician': {
        'core.view_patient',
        'core.read_imaging_request',
        'core.process_imaging',
        'core.read_imaging_report',
        *_ATTACHMENT_BASE,
    },

    # ── Anesthesia / OR ─────────────────────────────────────────────────────
    'Anesthesia Team': {
        'core.view_patient', 'core.view_visit',
        'core.read_clinical_note', 'core.read_prescription',
        'core.record_vital_signs', 'core.read_vital_signs',
        'core.write_anesthesia_record', 'core.read_anesthesia_record',
        'core.view_dept_inventory', 'core.view_dept_reports',
        *_SURGERY_READ,
        'core.write_surgery_anesthesia',
        *_ATTACHMENT_BASE,
    },

    # ── Pharmacy ────────────────────────────────────────────────────────────
    'Pharmacy Admin': {
        'core.view_patient',
        'core.read_prescription', 'core.read_prescription_for_dispensing',
        'core.read_medication_inventory', 'core.manage_medication',
        'core.manage_med_catalog', 'core.receive_stock',
        'core.manage_suppliers', 'core.view_inv_reports', 'core.export_inv_reports',
        'core.manage_supplier_payments',
        'core.dispense_medication',
        'core.process_pharmacy_sale', 'core.approve_pharmacy_discount',
        'core.approve_credit_sale', 'core.manage_pharmacy_returns',
        'core.read_pharmacy_reports',
        'core.perform_stock_count', 'core.approve_stock_count', 'core.manage_inventory_periods',
        *_DEPT_PHARM_MANAGER,
        *_ATTACHMENT_BASE,
    },
    'Pharmacist': {
        'core.view_patient',
        'core.read_prescription', 'core.read_prescription_for_dispensing',
        'core.read_medication_inventory',
        'core.dispense_medication',
        'core.process_pharmacy_sale',
        'core.manage_pharmacy_returns',
        'core.read_pharmacy_reports',
        'core.view_inv_reports',
        *_DEPT_PHARM_USER,
        *_ATTACHMENT_BASE,
    },
    'Pharmacy Sales': {
        'core.read_prescription_for_dispensing',
        'core.dispense_medication',
        'core.read_medication_inventory',
        'core.process_pharmacy_sale',
        'core.read_pharmacy_reports',
    },
    'Pharmacy Manager': {
        'core.view_patient',
        'core.read_prescription', 'core.read_prescription_for_dispensing',
        'core.read_medication_inventory',
        'core.dispense_medication',
        'core.process_pharmacy_sale', 'core.approve_pharmacy_discount',
        'core.approve_credit_sale', 'core.manage_pharmacy_returns',
        'core.read_pharmacy_reports', 'core.view_inv_reports',
        'core.manage_supplier_payments',
    },

    # ── Store / Inventory ───────────────────────────────────────────────────
    'Store Manager': {
        'core.read_inventory', 'core.manage_inventory',
        'core.create_purchase_order', 'core.approve_purchase_order',
        'core.create_purchase_request', 'core.approve_purchase_request',
        'core.issue_inventory', 'core.manage_inventory_batches',
        'core.manage_equipment_assets',
        'core.perform_stock_count', 'core.approve_stock_count',
        'core.view_store_reports', 'core.export_store_reports',
        'core.view_inv_reports', 'core.export_inv_reports',
        'core.manage_inventory_periods',
    },
    'Store Staff': {
        'core.read_inventory', 'core.manage_inventory',
        'core.create_purchase_order',
        'core.create_purchase_request',
        'core.issue_inventory', 'core.manage_inventory_batches',
        'core.perform_stock_count',
        'core.view_store_reports',
    },
    'Store Officer': {
        'core.read_inventory', 'core.manage_inventory',
        'core.create_purchase_request',
        'core.issue_inventory',
        'core.perform_stock_count',
        'core.view_store_reports',
    },
    'Asset Manager': {
        'core.read_inventory', 'core.manage_inventory',
        'core.manage_equipment_assets',
        'core.view_store_reports', 'core.export_store_reports',
    },

    # ── Finance ─────────────────────────────────────────────────────────────
    'Finance Manager': {
        'core.view_patient', 'core.view_visit',
        'core.read_billing', 'core.create_invoice', 'core.manage_billing',
        'core.process_payment',
        'core.approve_credit_invoice',
        'core.read_financial_report',
        'core.read_department_reports',
        *_SURGERY_READ,
        'core.generate_surgery_billing',
        'core.read_surgery_reports',
        *_PATIENT_FLOW_READ,
        'core.view_admission_dashboard', 'core.view_admission_reports',
    },
    'Finance Staff': {
        'core.view_patient', 'core.view_visit',
        'core.read_billing', 'core.create_invoice', 'core.manage_billing',
        'core.process_payment',
        'core.read_financial_report',
        *_SURGERY_READ,
        'core.generate_surgery_billing',
        *_PATIENT_FLOW_READ,
        'core.view_admission_dashboard',
    },
    'Cashier': {
        'core.view_patient', 'core.view_visit',
        'core.read_billing', 'core.create_invoice',
        'core.process_payment',
        'core.manage_cash_sessions',
        *_SURGERY_READ,
        *_PATIENT_FLOW_READ,
        'core.view_admission_dashboard',
        'core.process_surgery_booking_deposit',
        'core.collect_surgery_pre_deposit',
        'core.settle_surgery_account',
    },

    # ── HR ──────────────────────────────────────────────────────────────────
    'HR Manager': {
        'core.read_employee', 'core.manage_employees',
        'core.manage_attendance', 'core.manage_payroll',
        'core.read_department_reports',
        'core.manage_doctors', 'core.view_specialization_reports',
        'core.manage_employee_signatures',
    },
    'HR Staff': {
        'core.read_employee', 'core.manage_employees',
        'core.manage_attendance', 'core.manage_payroll',
    },
    'HR Officer': {
        'core.read_employee', 'core.manage_employees',
    },

    # ── Department Medication Stores ────────────────────────────────────────
    'Dept Medication Manager': {
        *_DEPT_PHARM_MANAGER,
        'core.perform_stock_count', 'core.approve_stock_count',
        'core.ward_supervisor_approve',
    },
    'Ward Store User': {
        *_DEPT_PHARM_USER,
    },
    'Emergency Store Staff': {
        *_DEPT_PHARM_USER,
        'core.manage_dept_stores',
    },
}

# ── Dashboard module catalogue ──────────────────────────────────────────────
# Each entry describes one module card on the dashboard.
# `permission`  — the perm checked to decide if the user sees this card.
# `available`   — False means "coming soon"; True means the URL is live.
DASHBOARD_MODULES = [
    {
        'id': 'receptionist',
        'title': 'Front Desk',
        'description': 'Appointments, patient registration, and front desk workflows',
        'url_name': 'receptionist_dashboard',
        'permission': 'core.read_appointment',
        'color': 'sky',
        'available': True,
        'icon_path': (
            'M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 '
            '0 00-2 2v12a2 2 0 002 2z'
        ),
    },
    {
        'id': 'doctor_module',
        'title': 'Doctor Module',
        'description': 'Patient chart, clinical notes, investigations, medications & procedures',
        'url_name': 'doctor_dashboard',
        'permission': 'core.write_clinical_note',
        'color': 'blue',
        'available': True,
        'icon_path': (
            'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 '
            '11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 '
            '10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-'
            '2.052-.382-3.016z'
        ),
    },
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
        'url_name': 'triage_dashboard',
        'permission': 'core.perform_triage',
        'color': 'red',
        'available': True,
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
        'url_name': 'queue_dashboard',
        'permission': 'core.manage_queue',
        'color': 'purple',
        'available': True,
        'icon_path': (
            'M4 6h16M4 10h16M4 14h16M4 18h16'
        ),
    },
    {
        'id': 'clinical_notes',
        'title': 'Clinical Notes',
        'description': 'Doctor and nursing clinical documentation',
        'url_name': 'doctor_dashboard',
        'permission': 'core.write_clinical_note',
        'color': 'indigo',
        'available': True,
        'icon_path': (
            'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 '
            '1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'
        ),
    },
    {
        'id': 'prescriptions',
        'title': 'Prescriptions',
        'description': 'Write and manage medication prescriptions',
        'url_name': 'doctor_dashboard',
        'permission': 'core.write_prescription',
        'color': 'teal',
        'available': True,
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
        'url_name': 'lab_dashboard',
        'permission': 'core.read_lab_request',
        'color': 'yellow',
        'available': True,
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
        'url_name': 'radiology_dashboard',
        'permission': 'core.read_imaging_request',
        'color': 'cyan',
        'available': True,
        'icon_path': (
            'M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 '
            '0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18'
        ),
    },
    {
        'id': 'patient_flow',
        'title': 'Patient Flow',
        'description': 'Track patient journeys from arrival to discharge across all departments',
        'url_name': 'patient_flow_dashboard',
        'permission': 'core.view_patient_flow',
        'color': 'teal',
        'available': True,
        'icon_path': (
            'M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 '
            '10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7'
        ),
    },
    {
        'id': 'surgery',
        'title': 'Surgery & Procedures',
        'description': 'Surgery ordering, OR scheduling, anesthesia, and operative documentation',
        'url_name': 'surgery_dashboard',
        'permission': 'core.read_surgery',
        'color': 'purple',
        'available': True,
        'icon_path': (
            'M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 '
            '014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 '
            '3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 '
            '1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 '
            '3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 '
            '01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 '
            '3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z'
        ),
    },
    {
        'id': 'certificates',
        'title': 'Medical & Death Certificates',
        'description': 'Issue, finalize, and print medical and death certificates',
        'url_name': 'certificate_dashboard',
        'permission': 'core.read_medical_certificate',
        'color': 'rose',
        'available': True,
        'icon_path': (
            'M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
        ),
    },
    {
        'id': 'anesthesia',
        'title': 'Anesthesia',
        'description': 'Anesthesia records and surgical preparation',
        'url_name': 'anesthesia_dashboard',
        'permission': 'core.read_anesthesia_record',
        'color': 'slate',
        'available': True,
        'icon_path': (
            'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 '
            '4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z'
        ),
    },
    {
        'id': 'pharmacy',
        'title': 'Pharmacy',
        'description': 'Medication dispensing and inventory',
        'url_name': 'pharmacy_dashboard',
        'permission': 'core.read_medication_inventory',
        'color': 'green',
        'available': True,
        'icon_path': (
            'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 '
            '2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 '
            '012-2h6a2 2 0 012 2v2M7 7h10'
        ),
    },
    {
        'id': 'nursing',
        'title': 'Nursing',
        'description': 'Ward patients, assessments, MAR, and shift handover',
        'url_name': 'nursing_dashboard',
        'permission': 'core.view_nursing_dashboard',
        'color': 'pink',
        'available': True,
        'icon_path': (
            'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 '
            '01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'
        ),
    },
    {
        'id': 'admission',
        'title': 'Admission',
        'description': 'Admission requests, deposits, and bed assignment',
        'url_name': 'admission_dashboard',
        'permission': 'core.view_admission_dashboard',
        'color': 'cyan',
        'available': True,
        'icon_path': (
            'M12 4.5v15m7.5-7.5h-15'
        ),
    },
    {
        'id': 'billing',
        'title': 'Billing & Finance',
        'description': 'Invoicing, payments, and financial records',
        'url_name': 'billing_dashboard',
        'permission': 'core.read_billing',
        'color': 'orange',
        'available': True,
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
        'url_name': 'inventory_dashboard',
        'permission': 'core.read_inventory',
        'color': 'amber',
        'available': True,
        'icon_path': (
            'M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 '
            '2h10a2 2 0 002-2V8m-9 4h4'
        ),
    },
    {
        'id': 'med_inventory',
        'title': 'Medication Inventory',
        'description': 'Drug stock, batches, expiry tracking and reports',
        'url_name': 'med_inventory_dashboard',
        'permission': 'core.read_medication_inventory',
        'color': 'teal',
        'available': True,
        'icon_path': (
            'M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 '
            '002-2v-4M9 21H5a2 2 0 01-2-2v-4m0 0h18'
        ),
    },
    {
        'id': 'dept_pharmacy',
        'title': 'Department Pharmacy',
        'description': 'Temporary medication stores for departments',
        'url_name': 'dept_pharmacy_dashboard',
        'permission': 'core.view_dept_inventory',
        'color': 'indigo',
        'available': True,
        'icon_path': (
            'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 '
            '0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2'
            'h6a2 2 0 012 2v2M7 7h10'
        ),
    },
    {
        'id': 'hr',
        'title': 'Human Resources',
        'description': 'Employee management and HR operations',
        'url_name': 'hr_dashboard',
        'permission': 'core.read_employee',
        'color': 'violet',
        'available': True,
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
        'url_name': 'reports_dashboard',
        'permission': 'core.read_clinical_reports',
        'color': 'rose',
        'available': True,
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

# ── Role → Department mapping (default department for each role) ──────────
ROLE_DEPARTMENTS = {
    # Administration
    'Administrator':         'Administration',
    'Medical Director':      'Medical Administration',
    # Reception
    'Receptionist':          'Reception / Front Desk',
    # Medical Records
    'Medical Records Officer': 'Medical Records',
    # Medical
    'Doctor':                'Medical Department',
    'Ward Doctor':           'Ward / Inpatient',
    'Emergency Doctor':      'Emergency Department',
    'Surgeon':               'Operating Room',
    # Nursing
    'Nurse':                 'Nursing Department',
    'Ward Nurse':            'Ward / Inpatient',
    'OR Nurse':              'Operating Room',
    'Emergency Nurse':       'Emergency Department',
    'Triage Staff':          'Emergency / Triage',
    'Ward Supervisor':       'Ward / Inpatient',
    # Laboratory
    'Laboratory Staff':      'Laboratory',
    'Lab Supervisor':        'Laboratory',
    # Radiology
    'Radiologist':           'Radiology',
    'Radiology Technician':  'Radiology',
    # Anesthesia
    'Anesthesia Team':       'Operating Room',
    # Pharmacy
    'Pharmacy Admin':        'Pharmacy',
    'Pharmacist':            'Pharmacy',
    'Pharmacy Sales':        'Pharmacy',
    # Store
    'Store Manager':         'Store / Inventory',
    'Store Staff':           'Store / Inventory',
    'Store Officer':         'Store / Inventory',
    'Asset Manager':         'Store / Inventory',
    # Finance
    'Finance Manager':       'Finance Department',
    'Finance Staff':         'Finance Department',
    'Cashier':               'Finance Department',
    # HR
    'HR Manager':            'Human Resources',
    'HR Staff':              'Human Resources',
    'HR Officer':            'Human Resources',
    # Dept Stores
    'Dept Medication Manager': 'Pharmacy',
    'Ward Store User':       'Ward / Inpatient',
    'Emergency Store Staff': 'Emergency Department',
}

# ── Default hospital departments to seed ──────────────────────────────────
DEPARTMENT_DEFAULTS = [
    # (name, dept_type)
    ('Administration',         'administration'),
    ('Medical Administration', 'administration'),
    ('Reception / Front Desk', 'administration'),
    ('Medical Records',        'medical_records'),
    ('Medical Department',     'clinical'),
    ('Nursing Department',     'nursing'),
    ('Emergency Department',   'emergency'),
    ('Emergency / Triage',     'emergency'),
    ('Laboratory',             'laboratory'),
    ('Radiology',              'radiology'),
    ('Operating Room',         'or'),
    ('Ward / Inpatient',       'ward'),
    ('Intensive Care Unit',    'icu'),
    ('Post-Operative Dept',    'ward'),
    ('Outpatient Dept (OPD)',  'opd'),
    ('Pharmacy',               'pharmacy'),
    ('Store / Inventory',      'store'),
    ('Finance Department',     'finance'),
    ('Human Resources',        'hr'),
    ('Maternity / Labour',     'maternity'),
    ('Pediatrics',             'pediatrics'),
    ('Surgical Ward',          'ward'),
    ('Medical Ward',           'ward'),
    ('NICU',                   'icu'),
]

# ── Role display metadata (badge colour + description) ─────────────────────
ROLE_META = {
    # Administration
    'Administrator':         {'badge': 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',         'desc': 'Full system administration'},
    'Medical Director':      {'badge': 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300', 'desc': 'Clinical oversight & direction'},
    # Reception
    'Receptionist':          {'badge': 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300',         'desc': 'Patient registration & appointments'},
    # Medical Records
    'Medical Records Officer': {'badge': 'bg-stone-100 text-stone-800 dark:bg-stone-900/40 dark:text-stone-300', 'desc': 'Patient document & attachment management'},
    # Medical
    'Doctor':                {'badge': 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',     'desc': 'Clinical diagnosis & treatment'},
    'Ward Doctor':           {'badge': 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',     'desc': 'Ward rounds & inpatient care'},
    'Emergency Doctor':      {'badge': 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',        'desc': 'Emergency assessment & treatment'},
    'Surgeon':               {'badge': 'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300', 'desc': 'Surgical procedures & planning'},
    # Nursing
    'Nurse':                 {'badge': 'bg-pink-100 text-pink-800 dark:bg-pink-900/40 dark:text-pink-300',     'desc': 'Patient care & monitoring'},
    'Ward Nurse':            {'badge': 'bg-pink-100 text-pink-800 dark:bg-pink-900/40 dark:text-pink-300',     'desc': 'Ward patient care & medication'},
    'OR Nurse':              {'badge': 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300', 'desc': 'OR surgical preparation'},
    'Emergency Nurse':       {'badge': 'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300',     'desc': 'Emergency patient care'},
    'Triage Staff':          {'badge': 'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300',     'desc': 'Emergency triage & assessment'},
    'Ward Supervisor':       {'badge': 'bg-pink-100 text-pink-800 dark:bg-pink-900/40 dark:text-pink-300',     'desc': 'Ward nursing oversight & request approval'},
    # Laboratory
    'Laboratory Staff':      {'badge': 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300', 'desc': 'Lab tests & results'},
    'Lab Supervisor':        {'badge': 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300', 'desc': 'Lab oversight & quality control'},
    # Radiology
    'Radiologist':           {'badge': 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-300',     'desc': 'Imaging studies & reports'},
    'Radiology Technician':  {'badge': 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300',     'desc': 'Imaging procedures & workflow'},
    # Anesthesia
    'Anesthesia Team':       {'badge': 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',    'desc': 'Anesthesia & surgical prep'},
    # Pharmacy
    'Pharmacy Admin':        {'badge': 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300', 'desc': 'Pharmacy & medication management'},
    'Pharmacist':            {'badge': 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300', 'desc': 'Dispensing & prescription verification'},
    'Pharmacy Sales':        {'badge': 'bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300',    'desc': 'Medication dispensing & sales'},
    # Store
    'Store Manager':         {'badge': 'bg-lime-100 text-lime-800 dark:bg-lime-900/40 dark:text-lime-300',    'desc': 'Inventory management & procurement'},
    'Store Staff':           {'badge': 'bg-lime-100 text-lime-700 dark:bg-lime-900/40 dark:text-lime-300',    'desc': 'Stock movement & operations'},
    'Store Officer':         {'badge': 'bg-lime-100 text-lime-700 dark:bg-lime-900/40 dark:text-lime-300',    'desc': 'Warehouse & stock counting'},
    'Asset Manager':         {'badge': 'bg-lime-100 text-lime-600 dark:bg-lime-900/40 dark:text-lime-200',    'desc': 'Equipment & asset management'},
    # Finance
    'Finance Manager':       {'badge': 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300', 'desc': 'Financial oversight & reports'},
    'Finance Staff':         {'badge': 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300', 'desc': 'Billing & financial management'},
    'Cashier':               {'badge': 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300', 'desc': 'Payment processing & receipts'},
    # HR
    'HR Manager':            {'badge': 'bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300', 'desc': 'HR oversight & management'},
    'HR Staff':              {'badge': 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300', 'desc': 'Employee & HR management'},
    'HR Officer':            {'badge': 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300', 'desc': 'Staff data entry & documents'},
    # Dept stores
    'Dept Medication Manager': {'badge': 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300', 'desc': 'Department medication store management'},
    'Ward Store User':       {'badge': 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/40 dark:text-indigo-300', 'desc': 'Ward stock requests & usage'},
    'Emergency Store Staff': {'badge': 'bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-300',        'desc': 'Emergency medication stock'},
}

# ── Role category groupings (for the role management UI) ─────────────────
ROLE_CATEGORIES = [
    {'label': 'Administration',             'roles': ['Administrator', 'Medical Director']},
    {'label': 'Reception',                  'roles': ['Receptionist']},
    {'label': 'Medical Records',            'roles': ['Medical Records Officer']},
    {'label': 'Medical / Clinical',         'roles': ['Doctor', 'Ward Doctor', 'Emergency Doctor', 'Surgeon']},
    {'label': 'Nursing',                    'roles': ['Nurse', 'Ward Nurse', 'OR Nurse', 'Emergency Nurse', 'Triage Staff', 'Ward Supervisor']},
    {'label': 'Laboratory',                 'roles': ['Laboratory Staff', 'Lab Supervisor']},
    {'label': 'Radiology',                  'roles': ['Radiologist', 'Radiology Technician']},
    {'label': 'Anesthesia / OR',            'roles': ['Anesthesia Team']},
    {'label': 'Pharmacy',                   'roles': ['Pharmacy Admin', 'Pharmacist', 'Pharmacy Sales']},
    {'label': 'Store / Inventory',          'roles': ['Store Manager', 'Store Staff', 'Store Officer', 'Asset Manager']},
    {'label': 'Finance',                    'roles': ['Finance Manager', 'Finance Staff', 'Cashier']},
    {'label': 'Human Resources',            'roles': ['HR Manager', 'HR Staff', 'HR Officer']},
    {'label': 'Department Medication Stores', 'roles': ['Dept Medication Manager', 'Ward Store User', 'Emergency Store Staff']},
]
