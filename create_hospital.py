import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hospital_system.settings')
django.setup()

from core.models import HospitalProfile

# Create hospital profile
hospital, created = HospitalProfile.objects.get_or_create(
    id=1,
    defaults={
        'name': 'Central Hospital',
        'short_name': 'CH',
        'tagline': 'Excellence in Healthcare',
        'address': '123 Medical Road',
        'phone': '+251-911-123456',
        'email': 'info@centralhospital.et',
        'website': 'www.centralhospital.et',
    }
)

if created:
    print(f"✓ Created hospital: {hospital.name}")
else:
    print(f"✓ Hospital already exists: {hospital.name}")
