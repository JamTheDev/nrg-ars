"""Thin views: parse, delegate to selectors/services, render.

See ARCHITECTURE.md section 6 for the URL and fragment design.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from reservations import selectors, services
from reservations.exceptions import BookingError, InvalidPassengerError
from reservations.models import Flight


def flight_list(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        'reservations/flight_list.html',
        {'rows': selectors.flight_rows()},
    )


def flight_detail(request: HttpRequest, flight_id: int) -> HttpResponse:
    flight = get_object_or_404(Flight, pk=flight_id)
    return render(
        request,
        'reservations/flight_detail.html',
        {
            'flight': flight,
            'cabin': selectors.seat_map(flight),
            'seat_fare': settings.SEAT_FARE,
            'currency_symbol': settings.CURRENCY_SYMBOL,
            'max_party_size': settings.MAX_PARTY_SIZE,
        },
    )


def _booking_response(
    request: HttpRequest,
    flight: Flight,
    *,
    message: str,
    ok: bool,
    selected: Iterable[str] = (),
) -> HttpResponse:
    """The refreshed cabin plus a status banner swapped out of band.

    Failures come back as 200, not 4xx: htmx does not swap error responses by
    default, so a 409 would leave the passenger staring at an unchanged page.
    Returning the whole map rather than the one clicked seat is what lets it
    self-heal when another kiosk booked in the meantime.
    """
    return render(
        request,
        'reservations/_booking_response.html',
        {
            'flight': flight,
            'cabin': selectors.seat_map(flight, selected=selected),
            'status_message': message,
            'status_ok': ok,
        },
    )


@require_GET
def seat_map(request: HttpRequest, flight_id: int) -> HttpResponse:
    """The cabin fragment, optionally with a party's seats already chosen.

    `?party=N` auto-picks N seats seated together where the cabin allows, and
    `?keep=12A,12B` holds on to seats the passenger chose by hand while the
    rest are filled in around them.
    """
    flight = get_object_or_404(Flight, pk=flight_id)
    party = request.GET.get('party')
    keep = [value for value in request.GET.get('keep', '').split(',') if value.strip()]

    if party is None:
        return _booking_response(request, flight, message='', ok=True, selected=keep)

    try:
        size = int(party)
    except ValueError:
        return _booking_response(
            request, flight, message='That is not a number of passengers.', ok=False, selected=keep
        )

    try:
        pick = selectors.pick_party_seats(flight, size, keep)
    except BookingError as exc:
        # The seats already chosen survive a refused change.
        return _booking_response(request, flight, message=str(exc), ok=False, selected=keep)

    message = '' if pick.is_together else 'Seated as close together as the cabin allows.'
    return _booking_response(
        request, flight, message=message, ok=True, selected=pick.designations
    )


@require_POST
def book_seat(request: HttpRequest, flight_id: int) -> HttpResponse:
    """Book the specific seats the passenger picked."""
    flight = get_object_or_404(Flight, pk=flight_id)
    designations = request.POST.getlist('seat')
    names = request.POST.getlist('passenger')

    if len(designations) != len(names):
        # A dropped pair would silently book the wrong seat for someone.
        return _booking_response(request, flight, message=str(InvalidPassengerError()), ok=False)

    requests = [
        services.SeatRequest(designation=designation, passenger_name=name)
        for designation, name in zip(designations, names, strict=True)
    ]

    try:
        bookings = services.book_seats(flight, requests)
    except BookingError as exc:
        return _booking_response(request, flight, message=str(exc), ok=False)

    booked = ', '.join(booking.seat.designation for booking in bookings)
    return _booking_response(request, flight, message=f'Booked {booked}.', ok=True)


@require_POST
def book_first(request: HttpRequest, flight_id: int) -> HttpResponse:
    """Take whatever seat is first free -- row order, then column order."""
    flight = get_object_or_404(Flight, pk=flight_id)

    try:
        booking = services.assign_first_available(flight, request.POST.get('passenger', ''))
    except BookingError as exc:
        return _booking_response(request, flight, message=str(exc), ok=False)

    return _booking_response(
        request, flight, message=f'Booked {booking.seat.designation}.', ok=True
    )
