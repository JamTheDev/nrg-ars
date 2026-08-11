"""Core booking domain -- see ARCHITECTURE.md section 1.

`Seat` is a shared catalog of cabin positions, not a per-flight copy: every
flight uses the same layout, so occupancy is a property of `Booking` rather
than of `Seat`. Availability is derived, never stored.
"""

from __future__ import annotations

from django.db import models


class Seat(models.Model):
    """One position in the shared cabin layout, e.g. row 12, column C."""

    row = models.PositiveSmallIntegerField()
    column = models.CharField(max_length=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['row', 'column'], name='unique_seat_position'),
        ]
        # Defines "first available": lowest row, then lowest column letter.
        ordering = ['row', 'column']

    def __str__(self) -> str:
        return self.designation

    @property
    def designation(self) -> str:
        return f'{self.row}{self.column}'


class Flight(models.Model):
    number = models.CharField(max_length=10, unique=True)
    origin = models.CharField(max_length=3)
    destination = models.CharField(max_length=3)
    departs_at = models.DateTimeField()

    class Meta:
        ordering = ['departs_at', 'number']

    def __str__(self) -> str:
        return f'{self.number} {self.origin}-{self.destination}'


class Passenger(models.Model):
    full_name = models.CharField(max_length=120)

    def __str__(self) -> str:
        return self.full_name


class Booking(models.Model):
    flight = models.ForeignKey(Flight, on_delete=models.CASCADE, related_name='bookings')
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name='bookings')
    passenger = models.ForeignKey(Passenger, on_delete=models.CASCADE, related_name='bookings')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # The system's correctness guarantee: one seat per flight, enforced by
        # the database rather than by a pre-check that can be raced past.
        constraints = [
            models.UniqueConstraint(fields=['flight', 'seat'], name='unique_seat_per_flight'),
        ]

    def __str__(self) -> str:
        return f'{self.passenger} on {self.flight.number} seat {self.seat.designation}'
