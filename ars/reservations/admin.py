from __future__ import annotations

from django.contrib import admin

from reservations.models import Booking, Flight, Passenger, Seat


@admin.register(Flight)
class FlightAdmin(admin.ModelAdmin):
    list_display = ['number', 'origin', 'destination', 'departs_at']
    list_filter = ['origin', 'destination']
    search_fields = ['number']


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ['designation', 'row', 'column']


@admin.register(Passenger)
class PassengerAdmin(admin.ModelAdmin):
    search_fields = ['full_name']


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['flight', 'seat', 'passenger', 'created_at']
    list_filter = ['flight']
