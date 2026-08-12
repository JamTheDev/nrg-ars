"""Seed a handful of flights so the departures screen has something to show.

Development convenience only. Departures are relative to *now*, so re-running
this refreshes a schedule that has fallen into the past -- which it will, since
demo data ages and an all-departed board is unbookable.

Existing flights are updated rather than duplicated, and their bookings are
untouched: a booking points at the flight, not at its departure time.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from reservations.models import Flight

# (number, origin, destination, days from now, hour of the day)
DEMO_FLIGHTS = [
    ('PR101', 'MNL', 'CEB', 0, 18),
    ('PR205', 'MNL', 'DVO', 1, 9),
    ('5J512', 'CEB', 'MNL', 2, 14),
    ('PR318', 'MNL', 'ILO', 4, 7),
    ('5J820', 'MNL', 'SIN', 7, 21),
    ('PR425', 'MNL', 'BCD', 10, 11),
    ('5J640', 'MNL', 'TAG', 13, 16),
]


class Command(BaseCommand):
    help = 'Create or refresh demo flights across the next two weeks.'

    def handle(self, *args, **options) -> None:
        today = timezone.localtime(timezone.now()).replace(
            minute=0, second=0, microsecond=0
        )
        created = refreshed = 0

        for number, origin, destination, days, hour in DEMO_FLIGHTS:
            departs_at = (today + timedelta(days=days)).replace(hour=hour)
            # A same-day flight whose hour has already passed would be born
            # departed, which is exactly the state this command exists to fix.
            if departs_at <= today:
                departs_at += timedelta(days=1)

            _, was_created = Flight.objects.update_or_create(
                number=number,
                defaults={
                    'origin': origin,
                    'destination': destination,
                    'departs_at': departs_at,
                },
            )
            created += was_created
            refreshed += not was_created

        last = max(f.departs_at for f in Flight.objects.filter(
            number__in=[row[0] for row in DEMO_FLIGHTS]
        ))
        self.stdout.write(
            self.style.SUCCESS(
                f'{created} created, {refreshed} refreshed. '
                f'Last departure {timezone.localtime(last):%a %d %b %H:%M}.'
            )
        )
