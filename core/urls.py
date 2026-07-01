from django.urls import path

from . import (
    views, views_admin, views_anesthesia, views_appointments, views_audit, views_billing,
    views_dept_pharmacy, views_doctor, views_hr, views_inventory, views_lab, views_med_inventory,
    views_or, views_patient_flow, views_pharmacy, views_pharmacy_pos, views_prescription,
    views_queue, views_radiology, views_receptionist, views_reports, views_store, views_surgery,
)

urlpatterns = [
    # ── Core app ────────────────────────────────────────────────────────────
    path('', views.dashboard, name='dashboard'),
    path('register-patient/', views.register_patient, name='register_patient'),
    path('patient-search/', views.patient_search, name='patient_search'),
    path('create-visit/<int:patient_id>/', views.create_visit, name='create_visit'),
    path('access-denied/', views.access_denied, name='access_denied'),

    # ── User management ──────────────────────────────────────────────────────
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/', views_admin.user_detail, name='user_detail'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/reset-password/', views.user_reset_password, name='user_reset_password'),
    path('users/<int:user_id>/toggle-active/', views.user_toggle_active, name='user_toggle_active'),

    # ── RBAC — Role management ─────────────────────────────────────────────
    path('admin/roles/', views_admin.role_list, name='role_list'),
    path('admin/roles/create/', views_admin.role_create, name='role_create'),
    path('admin/roles/<int:group_id>/', views_admin.role_detail, name='role_detail'),
    path('admin/roles/<int:group_id>/edit/', views_admin.role_permissions_edit, name='role_permissions_edit'),
    path('admin/roles/<int:group_id>/delete/', views_admin.role_delete, name='role_delete'),

    # ── RBAC — Department management ──────────────────────────────────────
    path('admin/departments/', views_admin.dept_list, name='dept_list'),
    path('admin/departments/create/', views_admin.dept_create, name='dept_create'),
    path('admin/departments/<int:dept_id>/edit/', views_admin.dept_edit, name='dept_edit'),
    path('admin/departments/<int:dept_id>/delete/', views_admin.dept_delete, name='dept_delete'),

    # ── Doctor module ─────────────────────────────────────────────────────────
    path('doctor/', views_doctor.doctor_dashboard, name='doctor_dashboard'),
    path('doctor/patients/', views_doctor.doctor_patient_list, name='doctor_patient_list'),
    path('doctor/visit/<int:visit_id>/', views_doctor.visit_detail, name='visit_detail'),
    path('doctor/visit/<int:visit_id>/notes/new/', views_doctor.clinical_note_create, name='clinical_note_create'),
    path('doctor/visit/<int:visit_id>/diagnosis/new/', views_doctor.diagnosis_create, name='diagnosis_create'),
    path('doctor/visit/<int:visit_id>/lab/new/', views_doctor.lab_order_create, name='lab_order_create'),
    path('doctor/visit/<int:visit_id>/imaging/new/', views_doctor.imaging_order_create, name='imaging_order_create'),
    path('doctor/visit/<int:visit_id>/medication/new/', views_doctor.medication_order_create, name='medication_order_create'),
    path('doctor/visit/<int:visit_id>/medication/<int:order_id>/discontinue/', views_doctor.medication_order_discontinue, name='medication_order_discontinue'),
    path('doctor/visit/<int:visit_id>/procedure/new/', views_doctor.procedure_order_create, name='procedure_order_create'),
    path('doctor/visit/<int:visit_id>/vitals/new/', views_doctor.vital_sign_create, name='vital_sign_create'),
    path('doctor/patient/<int:patient_id>/history/', views_doctor.patient_history, name='patient_history'),

    # ── Electronic Prescriptions (Doctor side) ──────────────────────────────
    path('prescriptions/visit/<int:visit_id>/new/', views_prescription.prescription_create, name='prescription_create'),
    path('prescriptions/<int:rx_id>/', views_prescription.prescription_detail, name='prescription_detail'),
    path('prescriptions/<int:rx_id>/send/', views_prescription.prescription_send, name='prescription_send'),
    path('prescriptions/<int:rx_id>/cancel/', views_prescription.prescription_cancel, name='prescription_cancel'),
    path('prescriptions/patient/<int:patient_id>/history/', views_prescription.prescription_history, name='prescription_history'),
    path('prescriptions/api/search/', views_prescription.medication_search_api, name='medication_search_api'),
    path('prescriptions/visit/<int:visit_id>/mar/', views_prescription.mar_list, name='mar_list'),
    path('prescriptions/mar/<int:entry_id>/update/', views_prescription.mar_update, name='mar_update'),

    # ── Electronic Prescriptions (Pharmacy side) ────────────────────────────
    path('pharmacy/rx/', views_prescription.rx_queue, name='rx_queue'),
    path('pharmacy/rx/<int:rx_id>/', views_prescription.rx_detail, name='rx_detail'),
    path('pharmacy/rx/<int:rx_id>/verify/', views_prescription.rx_verify, name='rx_verify'),
    path('pharmacy/rx/<int:rx_id>/dispense/', views_prescription.rx_dispense, name='rx_dispense'),
    path('pharmacy/rx/<int:rx_id>/print/', views_prescription.rx_print, name='rx_print'),
    path('pharmacy/rx/<int:rx_id>/confirm-dispense/', views_prescription.rx_confirm_dispense, name='rx_confirm_dispense'),

    # ── Queue & Triage module ─────────────────────────────────────────────────
    path('queue/', views_queue.queue_dashboard, name='queue_dashboard'),
    path('queue/call-next/', views_queue.queue_call_next, name='queue_call_next'),
    path('queue/<int:queue_id>/update-status/', views_queue.queue_update_status, name='queue_update_status'),
    path('triage/', views_queue.triage_dashboard, name='triage_dashboard'),
    path('triage/<int:visit_id>/assess/', views_queue.triage_create, name='triage_create'),

    # ── Laboratory module ────────────────────────────────────────────────────
    path('lab/', views_lab.lab_dashboard, name='lab_dashboard'),
    path('lab/reception/', views_lab.lab_reception, name='lab_reception'),
    path('lab/order/<int:order_id>/', views_lab.lab_order_detail, name='lab_order_detail'),
    path('lab/order/<int:order_id>/result/', views_lab.lab_result_enter, name='lab_result_enter'),
    path('lab/order/<int:order_id>/release/', views_lab.lab_result_release, name='lab_result_release'),
    path('lab/order/<int:order_id>/status/', views_lab.lab_order_update_status, name='lab_order_update_status'),
    path('lab/order/<int:order_id>/sample/', views_lab.lab_sample_collect, name='lab_sample_collect'),
    path('lab/order/<int:order_id>/cancel/', views_lab.lab_order_cancel, name='lab_order_cancel'),
    path('lab/services/', views_lab.lab_service_list, name='lab_service_list'),
    path('lab/services/new/', views_lab.lab_service_create, name='lab_service_create'),
    path('lab/services/<int:pk>/', views_lab.lab_service_detail, name='lab_service_detail'),
    path('lab/services/<int:pk>/edit/', views_lab.lab_service_edit, name='lab_service_edit'),

    # ── Radiology module ─────────────────────────────────────────────────────
    path('radiology/', views_radiology.radiology_dashboard, name='radiology_dashboard'),
    path('radiology/order/<int:order_id>/', views_radiology.imaging_order_detail, name='imaging_order_detail'),
    path('radiology/order/<int:order_id>/report/', views_radiology.imaging_report_enter, name='imaging_report_enter'),
    path('radiology/order/<int:order_id>/status/', views_radiology.imaging_order_update_status, name='imaging_order_update_status'),

    # ── Pharmacy module ──────────────────────────────────────────────────────
    path('pharmacy/', views_pharmacy.pharmacy_dashboard, name='pharmacy_dashboard'),
    path('pharmacy/stock/', views_pharmacy.pharmacy_stock_list, name='pharmacy_stock_list'),
    path('pharmacy/stock/new/', views_pharmacy.pharmacy_stock_create, name='pharmacy_stock_create'),
    path('pharmacy/stock/<int:stock_id>/edit/', views_pharmacy.pharmacy_stock_edit, name='pharmacy_stock_edit'),
    path('pharmacy/stock/<int:stock_id>/adjust/', views_pharmacy.pharmacy_stock_adjust, name='pharmacy_stock_adjust'),
    path('pharmacy/prescriptions/', views_pharmacy.pharmacy_prescriptions, name='pharmacy_prescriptions'),
    path('pharmacy/dispense/<int:order_id>/', views_pharmacy.pharmacy_dispense, name='pharmacy_dispense'),
    path('pharmacy/sales/', views_pharmacy.pharmacy_sales, name='pharmacy_sales'),

    # ── Pharmacy POS ────────────────────────────────────────────────────────────
    path('pharmacy/pos/', views_pharmacy_pos.pharmacy_pos, name='pharmacy_pos'),
    path('pharmacy/pos/create/', views_pharmacy_pos.pharmacy_sale_create, name='pharmacy_sale_create'),
    path('pharmacy/pos/search/product/', views_pharmacy_pos.pharmacy_product_search, name='pharmacy_product_search'),
    path('pharmacy/pos/search/patient/', views_pharmacy_pos.pharmacy_patient_search, name='pharmacy_patient_search'),
    path('pharmacy/pos/sales/', views_pharmacy_pos.pharmacy_sales_list, name='pharmacy_sales_list'),
    path('pharmacy/pos/sales/<int:sale_id>/', views_pharmacy_pos.pharmacy_sale_detail, name='pharmacy_sale_detail'),
    path('pharmacy/pos/sales/<int:sale_id>/receipt/', views_pharmacy_pos.pharmacy_sale_receipt, name='pharmacy_sale_receipt'),
    path('pharmacy/pos/sales/<int:sale_id>/dispense/', views_pharmacy_pos.pharmacy_sale_dispense, name='pharmacy_sale_dispense'),
    path('pharmacy/pos/sales/<int:sale_id>/cancel/', views_pharmacy_pos.pharmacy_sale_cancel, name='pharmacy_sale_cancel'),
    path('pharmacy/pos/sales/<int:sale_id>/return/', views_pharmacy_pos.pharmacy_return_create, name='pharmacy_return_create'),
    path('pharmacy/pos/returns/<int:return_id>/', views_pharmacy_pos.pharmacy_return_detail, name='pharmacy_return_detail'),
    path('pharmacy/pos/returns/<int:return_id>/approve/', views_pharmacy_pos.pharmacy_return_approve, name='pharmacy_return_approve'),
    # Reports
    path('pharmacy/reports/daily/', views_pharmacy_pos.pharmacy_report_daily, name='pharmacy_report_daily'),
    path('pharmacy/reports/medications/', views_pharmacy_pos.pharmacy_report_medications, name='pharmacy_report_medications'),
    path('pharmacy/reports/credit/', views_pharmacy_pos.pharmacy_report_credit, name='pharmacy_report_credit'),

    # ── Inventory module ─────────────────────────────────────────────────────
    path('inventory/', views_inventory.inventory_dashboard, name='inventory_dashboard'),
    path('inventory/items/', views_inventory.inventory_item_list, name='inventory_item_list'),
    path('inventory/items/new/', views_inventory.inventory_item_create, name='inventory_item_create'),
    path('inventory/items/<int:item_id>/edit/', views_inventory.inventory_item_edit, name='inventory_item_edit'),
    path('inventory/items/<int:item_id>/adjust/', views_inventory.inventory_item_adjust, name='inventory_item_adjust'),
    path('inventory/purchase-orders/', views_inventory.purchase_order_list, name='purchase_order_list'),
    path('inventory/purchase-orders/new/', views_inventory.purchase_order_create, name='purchase_order_create'),
    path('inventory/purchase-orders/<int:po_id>/', views_inventory.purchase_order_detail, name='purchase_order_detail'),
    path('inventory/purchase-orders/<int:po_id>/add-item/', views_inventory.purchase_order_add_item, name='purchase_order_add_item'),
    path('inventory/purchase-orders/<int:po_id>/status/', views_inventory.purchase_order_update_status, name='purchase_order_update_status'),

    # ── Enhanced Store: item detail, batches, transactions, equipment ─────────
    path('inventory/items/<int:pk>/', views_store.store_item_detail, name='store_item_detail'),
    path('inventory/items/<int:pk>/deactivate/', views_store.store_item_deactivate, name='store_item_deactivate'),
    path('inventory/items/<int:item_pk>/receive-batch/', views_store.store_batch_receive, name='store_batch_receive'),
    path('inventory/items/<int:item_pk>/issue/', views_store.store_issue_item, name='store_issue_item'),
    path('inventory/items/<int:item_pk>/manual-adjust/', views_store.store_item_adjust, name='store_item_adjust'),
    path('inventory/batches/', views_store.store_batch_list, name='store_batch_list'),
    path('inventory/batches/<int:pk>/dispose/', views_store.store_batch_dispose, name='store_batch_dispose'),
    path('inventory/transactions/', views_store.store_transaction_list, name='store_transaction_list'),

    # ── Equipment / Assets ─────────────────────────────────────────────────────
    path('inventory/equipment/', views_store.equipment_list, name='equipment_list'),
    path('inventory/equipment/new/', views_store.equipment_create, name='equipment_create'),
    path('inventory/equipment/<int:pk>/', views_store.equipment_detail, name='equipment_detail'),
    path('inventory/equipment/<int:pk>/edit/', views_store.equipment_edit, name='equipment_edit'),
    path('inventory/equipment/<int:asset_pk>/maintenance/', views_store.equipment_maintenance_add, name='equipment_maintenance_add'),

    # ── Purchase Requests ─────────────────────────────────────────────────────
    path('inventory/purchase-requests/', views_store.purchase_request_list, name='purchase_request_list'),
    path('inventory/purchase-requests/new/', views_store.purchase_request_create, name='purchase_request_create'),
    path('inventory/purchase-requests/<int:pk>/', views_store.purchase_request_detail, name='purchase_request_detail'),
    path('inventory/purchase-requests/<int:pk>/approve/', views_store.purchase_request_approve, name='purchase_request_approve'),
    path('inventory/purchase-requests/<int:pk>/convert/', views_store.purchase_request_convert, name='purchase_request_convert'),

    # ── Physical Stock Count ──────────────────────────────────────────────────
    path('inventory/stock-counts/', views_store.stock_count_list, name='stock_count_list'),
    path('inventory/stock-counts/new/', views_store.stock_count_create, name='stock_count_create'),
    path('inventory/stock-counts/<int:pk>/', views_store.stock_count_detail, name='stock_count_detail'),
    path('inventory/stock-counts/<int:pk>/enter/', views_store.stock_count_enter, name='stock_count_enter'),
    path('inventory/stock-counts/<int:pk>/approve/', views_store.stock_count_approve, name='stock_count_approve'),

    # ── Store Reports ────────────────────────────────────────────────────────
    path('inventory/reports/stock-on-hand/', views_store.report_store_stock_on_hand, name='report_store_stock_on_hand'),
    path('inventory/reports/low-stock/', views_store.report_store_low_stock, name='report_store_low_stock'),
    path('inventory/reports/expiry/', views_store.report_store_expiry, name='report_store_expiry'),
    path('inventory/reports/valuation/', views_store.report_store_valuation, name='report_store_valuation'),
    path('inventory/reports/transactions/', views_store.report_store_transactions, name='report_store_transactions'),
    path('inventory/reports/equipment/', views_store.report_store_equipment, name='report_store_equipment'),

    # ── Human Resources module ────────────────────────────────────────────────
    path('hr/', views_hr.hr_dashboard, name='hr_dashboard'),
    path('hr/employees/', views_hr.employee_list, name='employee_list'),
    path('hr/employees/new/', views_hr.employee_create, name='employee_create'),
    path('hr/employees/<int:employee_id>/', views_hr.employee_detail, name='employee_detail'),
    path('hr/employees/<int:employee_id>/edit/', views_hr.employee_edit, name='employee_edit'),
    path('hr/attendance/', views_hr.attendance_list, name='attendance_list'),
    path('hr/attendance/mark/', views_hr.attendance_mark, name='attendance_mark'),
    path('hr/leave/', views_hr.leave_request_list, name='leave_request_list'),
    path('hr/leave/new/', views_hr.leave_request_create, name='leave_request_create'),
    path('hr/leave/<int:request_id>/review/', views_hr.leave_request_review, name='leave_request_review'),

    # ── Anesthesia module ─────────────────────────────────────────────────────
    path('anesthesia/', views_anesthesia.anesthesia_dashboard, name='anesthesia_dashboard'),
    path('anesthesia/records/', views_anesthesia.anesthesia_record_list, name='anesthesia_record_list'),
    path('anesthesia/visit/<int:visit_id>/new/', views_anesthesia.anesthesia_record_create, name='anesthesia_record_create'),
    path('anesthesia/record/<int:record_id>/', views_anesthesia.anesthesia_record_detail, name='anesthesia_record_detail'),
    path('anesthesia/record/<int:record_id>/edit/', views_anesthesia.anesthesia_record_edit, name='anesthesia_record_edit'),

    # ── Billing module ────────────────────────────────────────────────────────
    path('billing/', views_billing.billing_dashboard, name='billing_dashboard'),
    path('billing/invoices/', views_billing.invoice_list, name='invoice_list'),
    path('billing/split/', views_billing.invoice_split_view, name='invoice_split_view'),
    path('billing/panels/paid/', views_billing.invoice_panel_paid, name='invoice_panel_paid'),
    path('billing/panels/unpaid/', views_billing.invoice_panel_unpaid, name='invoice_panel_unpaid'),
    path('billing/invoices/new/', views_billing.invoice_create, name='invoice_create'),
    path('billing/invoices/<int:invoice_id>/', views_billing.invoice_detail, name='invoice_detail'),
    path('billing/invoices/<int:invoice_id>/add-item/', views_billing.invoice_add_item, name='invoice_add_item'),
    path('billing/items/<int:item_id>/delete/', views_billing.invoice_delete_item, name='invoice_delete_item'),
    path('billing/invoices/<int:invoice_id>/status/', views_billing.invoice_update_status, name='invoice_update_status'),
    path('billing/invoices/<int:invoice_id>/payment/', views_billing.payment_create, name='payment_create'),
    path('billing/invoices/<int:invoice_id>/payment/<int:payment_id>/receipt/', views_billing.invoice_receipt, name='invoice_receipt'),
    path('billing/invoices/<int:invoice_id>/discount/', views_billing.invoice_apply_discount, name='invoice_apply_discount'),
    # Credit
    path('billing/invoices/<int:invoice_id>/credit/', views_billing.credit_invoice_create, name='credit_invoice_create'),
    path('billing/invoices/<int:invoice_id>/credit/approve/', views_billing.credit_approve, name='credit_approve'),
    path('billing/credit/', views_billing.credit_list, name='credit_list'),
    # Cash sessions
    path('billing/cash-sessions/', views_billing.cash_session_list, name='cash_session_list'),
    path('billing/cash-sessions/open/', views_billing.cash_session_open, name='cash_session_open'),
    path('billing/cash-sessions/<int:session_id>/close/', views_billing.cash_session_close, name='cash_session_close'),
    # Reports
    path('billing/reports/cash/', views_billing.report_cash_collection, name='report_cash_collection'),
    path('billing/reports/credit/', views_billing.report_credit, name='report_credit'),
    path('billing/reports/outstanding/', views_billing.report_outstanding, name='report_outstanding'),

    # ── Receptionist / Front Desk module ─────────────────────────────────────
    path('receptionist/', views_receptionist.receptionist_dashboard, name='receptionist_dashboard'),
    path('receptionist/appointments/', views_receptionist.appointment_list, name='appointment_list'),
    path('receptionist/appointments/new/', views_receptionist.appointment_create, name='appointment_create'),
    path('receptionist/appointments/<int:appt_id>/', views_receptionist.appointment_detail, name='appointment_detail'),
    path('receptionist/appointments/<int:appt_id>/edit/', views_receptionist.appointment_edit, name='appointment_edit'),
    path('receptionist/appointments/<int:appt_id>/start-visit/', views_receptionist.appointment_start_visit, name='appointment_start_visit'),
    path('receptionist/appointments/<int:appt_id>/confirm/', views_receptionist.appointment_confirm, name='appointment_confirm'),
    path('receptionist/appointments/<int:appt_id>/cancel/', views_receptionist.appointment_cancel, name='appointment_cancel'),
    path('receptionist/appointments/<int:appt_id>/mark-arrived/', views_receptionist.appointment_mark_arrived, name='appointment_mark_arrived'),
    path('receptionist/doctor-schedules/', views_receptionist.doctor_schedule_view, name='doctor_schedule_view'),
    path('receptionist/doctor-schedules/<int:doctor_id>/edit/', views_receptionist.doctor_schedule_edit, name='doctor_schedule_edit'),
    path('receptionist/api/doctor-availability/', views_receptionist.doctor_availability_api, name='doctor_availability_api'),
    path('receptionist/patient-flow/', views_receptionist.patient_flow, name='patient_flow'),

    # ── Consultation Appointment Scheduling ──────────────────────────────────
    path('appointments/', views_appointments.consultation_dashboard, name='consultation_dashboard'),
    path('appointments/list/', views_appointments.appt_list, name='appt_list'),
    path('appointments/new/', views_appointments.appt_create, name='appt_create'),
    path('appointments/<int:pk>/', views_appointments.appt_detail, name='appt_detail'),
    path('appointments/<int:pk>/edit/', views_appointments.appt_edit, name='appt_edit'),
    path('appointments/<int:pk>/cancel/', views_appointments.appt_cancel, name='appt_cancel'),
    path('appointments/<int:pk>/reschedule/', views_appointments.appt_reschedule, name='appt_reschedule'),
    path('appointments/<int:pk>/no-show/', views_appointments.appt_no_show, name='appt_no_show'),
    path('appointments/doctor-availability/', views_appointments.doctor_availability_list, name='doctor_availability_list'),
    path('appointments/doctor-availability/<int:doctor_id>/edit/', views_appointments.doctor_availability_edit, name='doctor_availability_edit'),
    path('appointments/doctor-availability/<int:doctor_id>/exceptions/', views_appointments.doctor_availability_exception, name='doctor_availability_exception'),
    path('appointments/api/slots/', views_appointments.get_doctor_slots, name='get_doctor_slots'),
    path('appointments/reports/daily-schedule/', views_appointments.report_appt_daily_schedule, name='report_appt_daily_schedule'),
    path('appointments/reports/dept-schedule/', views_appointments.report_appt_dept_schedule, name='report_appt_dept_schedule'),
    path('appointments/reports/list/', views_appointments.report_appt_list, name='report_appt_list'),
    path('appointments/reports/cancelled/', views_appointments.report_appt_cancelled, name='report_appt_cancelled'),
    path('appointments/reports/no-show/', views_appointments.report_appt_no_show, name='report_appt_no_show'),
    path('appointments/reports/workload/', views_appointments.report_appt_workload, name='report_appt_workload'),

    # ── Medication Inventory module ───────────────────────────────────────────
    path('med-inventory/', views_med_inventory.med_inventory_dashboard, name='med_inventory_dashboard'),
    path('med-inventory/medications/', views_med_inventory.medication_list, name='medication_list'),
    path('med-inventory/medications/new/', views_med_inventory.medication_create, name='medication_create'),
    path('med-inventory/medications/<int:med_id>/', views_med_inventory.medication_detail, name='medication_detail'),
    path('med-inventory/medications/<int:med_id>/edit/', views_med_inventory.medication_edit, name='medication_edit'),
    path('med-inventory/medications/<int:med_id>/deactivate/', views_med_inventory.medication_deactivate, name='medication_deactivate'),
    path('med-inventory/batches/', views_med_inventory.batch_list, name='batch_list'),
    path('med-inventory/receive/', views_med_inventory.batch_receive_select, name='batch_receive_select'),
    path('med-inventory/medications/<int:med_id>/receive/', views_med_inventory.batch_receive, name='batch_receive'),
    path('med-inventory/batches/<int:batch_id>/dispose/', views_med_inventory.batch_dispose, name='batch_dispose'),
    path('med-inventory/adjustment/', views_med_inventory.stock_adjustment, name='stock_adjustment'),
    path('med-inventory/suppliers/', views_med_inventory.supplier_list, name='supplier_list'),
    path('med-inventory/suppliers/new/', views_med_inventory.supplier_create, name='supplier_create'),
    path('med-inventory/suppliers/<int:supplier_id>/edit/', views_med_inventory.supplier_edit, name='supplier_edit'),
    path('med-inventory/transactions/', views_med_inventory.transaction_list, name='transaction_list'),
    path('med-inventory/reports/stock-on-hand/', views_med_inventory.report_stock_on_hand, name='report_stock_on_hand'),
    path('med-inventory/reports/expiry/', views_med_inventory.report_expiry, name='report_expiry'),
    path('med-inventory/reports/stock-movement/', views_med_inventory.report_stock_movement, name='report_stock_movement'),
    path('med-inventory/reports/low-stock/', views_med_inventory.report_low_stock, name='report_low_stock'),
    path('med-inventory/reports/stock-card/', views_med_inventory.report_stock_card, name='report_stock_card'),
    path('med-inventory/reports/valuation/', views_med_inventory.report_valuation, name='report_valuation'),

    # ── Department Pharmacy module ────────────────────────────────────────────
    path('dept-pharmacy/', views_dept_pharmacy.dept_pharmacy_dashboard, name='dept_pharmacy_dashboard'),

    # Stores
    path('dept-pharmacy/stores/', views_dept_pharmacy.dept_store_list, name='dept_store_list'),
    path('dept-pharmacy/stores/new/', views_dept_pharmacy.dept_store_create, name='dept_store_create'),
    path('dept-pharmacy/stores/<int:store_id>/', views_dept_pharmacy.dept_store_detail, name='dept_store_detail'),
    path('dept-pharmacy/stores/<int:store_id>/edit/', views_dept_pharmacy.dept_store_edit, name='dept_store_edit'),

    # Transfer Requests
    path('dept-pharmacy/requests/', views_dept_pharmacy.transfer_request_list, name='transfer_request_list'),
    path('dept-pharmacy/requests/new/', views_dept_pharmacy.transfer_request_create, name='transfer_request_create'),
    path('dept-pharmacy/requests/<int:req_id>/', views_dept_pharmacy.transfer_request_detail, name='transfer_request_detail'),
    path('dept-pharmacy/requests/<int:req_id>/approve/', views_dept_pharmacy.transfer_request_approve, name='transfer_request_approve'),
    path('dept-pharmacy/requests/<int:req_id>/fulfill/', views_dept_pharmacy.transfer_request_fulfill, name='transfer_request_fulfill'),

    # Transfers
    path('dept-pharmacy/transfers/', views_dept_pharmacy.dept_transfer_list, name='dept_transfer_list'),
    path('dept-pharmacy/transfers/<int:transfer_id>/', views_dept_pharmacy.dept_transfer_detail, name='dept_transfer_detail'),
    path('dept-pharmacy/transfers/<int:transfer_id>/receive/', views_dept_pharmacy.dept_transfer_receive, name='dept_transfer_receive'),

    # Returns
    path('dept-pharmacy/stores/<int:store_id>/return/', views_dept_pharmacy.dept_return_create, name='dept_return_create'),

    # Usage
    path('dept-pharmacy/usage/', views_dept_pharmacy.dept_usage_list, name='dept_usage_list'),
    path('dept-pharmacy/stores/<int:store_id>/usage/new/', views_dept_pharmacy.dept_usage_create, name='dept_usage_create'),

    # Reports
    path('dept-pharmacy/reports/stock-on-hand/', views_dept_pharmacy.report_dept_stock_on_hand, name='dept_report_stock_on_hand'),
    path('dept-pharmacy/reports/stock-card/', views_dept_pharmacy.report_dept_stock_card, name='dept_report_stock_card'),
    path('dept-pharmacy/reports/consumption/', views_dept_pharmacy.report_dept_consumption, name='dept_report_consumption'),
    path('dept-pharmacy/reports/transfers/', views_dept_pharmacy.report_transfer_history, name='dept_report_transfer_history'),

    # ── Reports & Analytics module ────────────────────────────────────────────
    path('reports/', views_reports.reports_hub, name='reports_hub'),
    path('reports/', views_reports.reports_hub, name='reports_dashboard'),  # backward compat

    # Management reports
    path('reports/executive/', views_reports.report_executive, name='report_executive'),
    path('reports/revenue/', views_reports.report_revenue, name='report_revenue'),
    path('reports/invoice-status/', views_reports.report_invoice_status, name='report_invoice_status'),

    # Patient reports
    path('reports/patients/registration/', views_reports.report_patient_registration, name='report_patient_registration'),
    path('reports/patients/visits/', views_reports.report_patient_visits, name='report_patient_visits'),
    path('reports/patients/', views_reports.report_patients, name='report_patients'),

    # Clinical reports
    path('reports/clinical/doctor-performance/', views_reports.report_doctor_performance, name='report_doctor_performance'),
    path('reports/clinical/diagnosis/', views_reports.report_diagnosis, name='report_diagnosis'),
    path('reports/clinical/lab/', views_reports.report_lab_activity, name='report_lab_activity'),
    path('reports/clinical/radiology/', views_reports.report_radiology_activity, name='report_radiology_activity'),
    path('reports/clinical/', views_reports.report_clinical, name='report_clinical'),

    # Pharmacy reports
    path('reports/pharmacy/summary/', views_reports.report_pharmacy_summary, name='report_pharmacy_summary'),
    path('reports/pharmacy/prescriptions/', views_reports.report_prescriptions, name='report_prescriptions'),

    # Finance/staff
    path('reports/financial/', views_reports.report_financial, name='report_financial'),
    path('reports/staff/', views_reports.report_staff, name='report_staff'),
    path('reports/hr/employees/', views_reports.report_employees, name='report_employees'),
    path('reports/audit/activity/', views_reports.report_audit_activity, name='report_audit_activity'),

    # ── Surgery & Procedure module ────────────────────────────────────────────
    path('surgery/', views_surgery.surgery_dashboard, name='surgery_dashboard'),
    path('surgery/orders/', views_surgery.surgery_order_list, name='surgery_order_list'),
    path('surgery/orders/new/', views_surgery.surgery_order_create, name='surgery_order_create'),
    path('surgery/orders/new/patient/<int:patient_id>/', views_surgery.surgery_order_create, name='surgery_order_create_patient'),
    path('surgery/orders/new/visit/<int:visit_id>/', views_surgery.surgery_order_create, name='surgery_order_create_visit'),
    path('surgery/orders/<int:order_id>/', views_surgery.surgery_order_detail, name='surgery_order_detail'),
    path('surgery/orders/<int:order_id>/edit/', views_surgery.surgery_order_edit, name='surgery_order_edit'),
    path('surgery/orders/<int:order_id>/status/', views_surgery.surgery_order_update_status, name='surgery_order_update_status'),
    path('surgery/orders/<int:order_id>/schedule/', views_surgery.surgery_schedule_create, name='surgery_schedule_create'),
    path('surgery/orders/<int:order_id>/anesthesia/', views_surgery.surgery_anesthesia_create, name='surgery_anesthesia_create'),
    path('surgery/orders/<int:order_id>/operative-note/', views_surgery.operative_note_create, name='operative_note_create'),
    path('surgery/orders/<int:order_id>/consumable/', views_surgery.surgery_consumable_add, name='surgery_consumable_add'),
    path('surgery/orders/<int:order_id>/billing/', views_surgery.surgery_billing_generate, name='surgery_billing_generate'),
    # OR Management (surgery module — legacy; use /or/ for full OR scheduling)
    path('surgery/or/', views_surgery.or_dashboard, name='surgery_or_dashboard'),
    path('surgery/or/rooms/', views_surgery.or_room_list, name='surgery_or_room_list'),
    path('surgery/or/rooms/new/', views_surgery.or_room_create, name='surgery_or_room_create'),
    path('surgery/or/rooms/<int:room_id>/edit/', views_surgery.or_room_edit, name='surgery_or_room_edit'),
    # Procedure Master
    path('surgery/procedures/', views_surgery.procedure_master_list, name='procedure_master_list'),
    path('surgery/procedures/new/', views_surgery.procedure_master_create, name='procedure_master_create'),
    path('surgery/procedures/<int:proc_id>/', views_surgery.procedure_master_detail, name='procedure_master_detail'),
    path('surgery/procedures/<int:proc_id>/edit/', views_surgery.procedure_master_edit, name='procedure_master_edit'),
    # Surgery Reports
    path('surgery/reports/', views_surgery.surgery_reports, name='surgery_reports'),
    path('surgery/reports/schedule/', views_surgery.report_surgery_schedule, name='report_surgery_schedule'),
    path('surgery/reports/completed/', views_surgery.report_completed_surgeries, name='report_completed_surgeries'),
    path('surgery/reports/cancelled/', views_surgery.report_cancelled_surgeries, name='report_cancelled_surgeries'),
    path('surgery/reports/surgeon-performance/', views_surgery.report_surgeon_performance, name='report_surgeon_performance'),
    path('surgery/reports/or-utilization/', views_surgery.report_or_utilization, name='report_or_utilization'),
    # API endpoints
    path('surgery/api/procedure-info/<int:proc_id>/', views_surgery.api_procedure_info, name='api_procedure_info'),

    # ── Patient Flow module ───────────────────────────────────────────────────
    path('flow/', views_patient_flow.patient_flow_dashboard, name='patient_flow_dashboard'),
    path('flow/visits/', views_patient_flow.patient_flow_list, name='patient_flow_list'),
    path('flow/visits/<int:visit_id>/', views_patient_flow.patient_journey_detail, name='patient_journey_detail'),
    path('flow/visits/<int:visit_id>/status/', views_patient_flow.visit_status_update, name='visit_status_update'),
    path('flow/visits/<int:visit_id>/discharge/', views_patient_flow.discharge_create, name='discharge_create'),
    path('flow/worklist/', views_patient_flow.dept_worklist, name='dept_worklist'),
    path('flow/api/stats/', views_patient_flow.flow_stats_api, name='flow_stats_api'),

    # ── Audit Logs ──────────────────────────────────────────────────────────
    path('audit/', views_audit.audit_dashboard, name='audit_dashboard'),
    path('audit/logs/', views_audit.audit_log_list, name='audit_log_list'),
    path('audit/logs/<int:log_id>/', views_audit.audit_log_detail, name='audit_log_detail'),
    path('audit/reports/user-activity/', views_audit.report_user_activity, name='report_user_activity'),
    path('audit/reports/financial/', views_audit.report_financial_audit, name='report_financial_audit'),
    path('audit/reports/inventory/', views_audit.report_inventory_audit, name='report_inventory_audit'),
    path('audit/reports/patient-access/', views_audit.report_patient_access, name='report_patient_access'),

    # ── OR Scheduling module ─────────────────────────────────────────────────
    path('or/', views_or.or_dashboard, name='or_dashboard'),
    path('or/surgery-requests/', views_or.surgery_request_list, name='surgery_request_list'),
    path('or/surgery-requests/new/', views_or.surgery_request_create, name='surgery_request_create'),
    path('or/surgery-requests/<int:pk>/', views_or.surgery_request_detail, name='surgery_request_detail'),
    path('or/schedule/', views_or.or_schedule_list, name='or_schedule_list'),
    path('or/schedule/new/', views_or.or_schedule_create, name='or_schedule_create'),
    path('or/schedule/<int:pk>/', views_or.or_schedule_detail, name='or_schedule_detail'),
    path('or/schedule/<int:pk>/edit/', views_or.or_schedule_edit, name='or_schedule_edit'),
    path('or/calendar/', views_or.or_calendar, name='or_calendar'),
    path('or/rooms/', views_or.or_room_list, name='or_room_list'),
    path('or/rooms/new/', views_or.or_room_create, name='or_room_create'),
    path('or/rooms/<int:pk>/edit/', views_or.or_room_edit, name='or_room_edit'),
    path('or/check-conflicts/', views_or.check_or_conflicts, name='check_or_conflicts'),
    path('or/api/patient-search/', views_or.patient_search_ajax, name='or_patient_search_ajax'),
    # OR Reports
    path('or/reports/daily/', views_or.report_or_daily, name='report_or_daily'),
    path('or/reports/utilization/', views_or.report_or_utilization, name='report_or_utilization'),
    path('or/reports/surgeon-schedule/', views_or.report_surgeon_schedule, name='report_surgeon_schedule'),
]
