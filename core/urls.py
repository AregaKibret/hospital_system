from django.urls import path

from . import views

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
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/reset-password/', views.user_reset_password, name='user_reset_password'),
    path('users/<int:user_id>/toggle-active/', views.user_toggle_active, name='user_toggle_active'),
]
