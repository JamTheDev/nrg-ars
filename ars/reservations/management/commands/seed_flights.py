"""Seed a handful of flights so the departures screen has something to show.

Development convenience only -- idempotent on flight number, so re-running it
never duplicates a flight.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reservations.models import Flight

# (number, origin, destination, hours from now)
DEMO_FLIGHTS = [
    ('PR101', 'MNL', 'CEB', 6),
    ('PR205', 'MNL', 'DVO', 27),
    ('5J512', 'CEB', 'MNL', 32),
    ('PR318', 'MNL', 'ILO', 51),
    ('5J820', 'MNL', 'SIN', 74),
]


class Command(BaseCommand):
    help = 'Create demo flights if they do not already exist.'

    def handle(self, *args, **options) -> None:
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        created = 0
        for number, origin, destination, offset_hours in DEMO_FLIGHTS:
            _, was_created = Flight.objects.get_or_create(
                number=number,
                defaults={
                    'origin': origin,
                    'destination': destination,
                    'departs_at': now + timedelta(hours=offset_hours),
                },
            )
            created += was_created

        self.stdout.write(
            self.style.SUCCESS(
                f'{created} flight(s) created, {len(DEMO_FLIGHTS) - created} already present.'
            )
        )
