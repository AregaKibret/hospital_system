# API Documentation

## There Is No Formal REST/JSON API

This is a traditional, server-rendered Django application — every user-facing page is full HTML
returned from a Django view, not a JSON payload consumed by a separate frontend. There is no
Django REST Framework installed, no API versioning scheme, no API authentication token system
(login is the standard Django session-cookie auth used by the whole site), and consequently:

- **No OpenAPI/Swagger specification** exists or can be meaningfully auto-generated — there's no
  DRF `Serializer`/`ViewSet` layer to introspect.
- **No Postman collection** exists for the same reason.
- If you need either of these for integration work, they would need to be built as a genuinely
  new API layer (most realistically with Django REST Framework added as a dependency), not
  generated from what's here today.

## What Actually Exists: In-Page AJAX Endpoints

A small number of views return `JsonResponse` instead of HTML — these exist purely to power
specific UI widgets (live search boxes, availability pickers, polling) on otherwise
server-rendered pages. They are **not** a public/external API: they require the same login
session and permission checks as everything else, are not documented for third-party
consumption, and their response shape can change without notice as the UI evolves.

| Endpoint | View | Purpose | Auth |
|---|---|---|---|
| `GET /notifications/unread-count/` | `views_session.notifications_unread_count` | Polled by the bell-icon widget in the header on every page | Login required |
| `GET /lab/api/search/?q=` | `views_lab.lab_service_search_api` | Live test search for the multi-test lab-ordering cart | `core.request_lab_test` |
| `GET /radiology/api/search/?q=` | `views_radiology.imaging_service_search_api` | Live imaging-service search | `core.request_imaging` |
| `GET /pharmacy/api/search/?q=` | `views_pharmacy.pharmacy_stock_search_api` | Live medication search at POS | `core.process_pharmacy_sale` |
| `GET /prescriptions/api/search/?q=` | `views_prescription.medication_search_api` | Live medication search when writing a prescription | `core.write_prescription` |
| `GET /appointments/api/slots/?doctor_id=&date=` | `views_appointments.get_doctor_slots` | Open appointment slots for a doctor on a date | `core.read_appointment` |
| `GET /appointments/api/patient-search/?q=` | `views_appointments.appt_patient_search` | Patient lookup while booking | `core.read_appointment` |
| `GET /receptionist/api/doctor-availability/?doctor_id=` | `views_receptionist.doctor_availability_api` | Alternate doctor-slot lookup used by the Reception booking screen | `core.manage_appointments` |
| `GET /settings/specializations/api/for-department/?department_id=` | `views_specialization.specializations_for_department` | Populates the Specialization dropdown when a Department is chosen | `core.manage_doctors` / `core.manage_specializations` |
| `GET /surgery/api/procedure-info/<id>/` | `views_surgery.api_procedure_info` | Procedure Master lookup detail | `core.order_surgery` |
| `GET /flow/api/stats/` | `views_patient_flow.flow_stats_api` | Live counters on the Patient Flow dashboard | `core.view_patient_flow` |
| `GET /or/api/patient-search/?q=` | `views_or.patient_search_ajax` | Patient lookup on the OR scheduling screen | `core.manage_or_schedule` |

All twelve follow the same shape: a `GET` request with a `q`/id-style query parameter, a
`JsonResponse` with a small list of matching objects (`id`, display label, and a few relevant
fields), consumed by inline `fetch()`/`XMLHttpRequest` JavaScript in the corresponding template.
None of them accept `POST`, and none of them exist outside the specific page that uses them.

## Authentication (for these AJAX endpoints)

Same as the rest of the site: Django's session-cookie authentication
(`django.contrib.auth.middleware.AuthenticationMiddleware`), enforced per-view by
`@hms_permission_required(...)` (see `core/decorators.py`) exactly like every HTML view. There
is no separate API key, token, or OAuth mechanism.

## Error Codes, Validation, Pagination, Filtering, Sorting

These AJAX endpoints are minimal by design — they don't implement a general error-code
convention, structured validation-error responses, or pagination/sorting query parameters, all
of which are hallmarks of a real API layer that doesn't exist here. If you need to add a proper
API (e.g. for a future mobile app or third-party integration), treat it as new development —
adding Django REST Framework, a serializer per model you want to expose, real API-key or token
authentication, and a proper error/pagination convention — rather than an extension of the
endpoints above.
