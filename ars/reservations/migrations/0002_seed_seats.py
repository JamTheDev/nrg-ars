"""Seed the shared Seat catalog from the cabin layout settings.

CABIN_ROWS/CABIN_COLUMNS are read here once. After this migration the seeded
rows are the source of truth -- changing the settings requires a new migration,
not a config edit. See ARCHITECTURE.md section 1.
"""

from django.conf import settings
from django.db import migrations


def seed_seats(apps, schema_editor):
    Seat = apps.get_model('reservations', 'Seat')
    Seat.objects.bulk_create(
        [
            Seat(row=row, column=column)
            for row in range(1, settings.CABIN_ROWS + 1)
            for column in settings.CABIN_COLUMNS
        ],
        ignore_conflicts=True,
    )


def unseed_seats(apps, schema_editor):
    Seat = apps.get_model('reservations', 'Seat')
    Seat.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('reservations', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_seats, unseed_seats),
    ]
