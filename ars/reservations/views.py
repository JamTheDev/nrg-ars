"""Thin views: parse, delegate to selectors/services, render.

See ARCHITECTURE.md section 6 for the URL and fragment design.
"""

from __future__ import annotations

from collections.abc import Iterable

from assistant import answers, extraction
from assistant.prompts import UNSAFE_REPLY
from assistant.providers import ProviderUnavailable
from assistant.safety import UnsafeRequest
from assistant.schema import describe
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


def _search_response(
    request: HttpRequest, flight: Flight, prose: str, party: str | None, keep: list[str]
) -> HttpResponse:
    """Answer a natural-language seat request.

    Every failure returns the map unchanged with the passenger's existing
    selection intact: smart search is a shortcut, and losing your seats
    because a model was slow would make it a liability.
    """
    try:
        query = extraction.extract(prose)
    except ProviderUnavailable:
        return _booking_response(
            request,
            flight,
            message='Smart search is unavailable right now — pick a seat on the map.',
            ok=False,
            selected=keep,
        )
    except UnsafeRequest:
        # Nothing about why: a screening message that explains itself is a
        # tutorial for the next attempt.
        return _booking_response(
            request, flight, message=UNSAFE_REPLY, ok=False, selected=keep
        )

    # A question wants an answer, not a selection. Every number in the reply
    # is counted from the database; the model only says what to count.
    if query.intent in ('count', 'list', 'status'):
        return _booking_response(
            request,
            flight,
            message=answers.answer(flight, query),
            ok=True,
            selected=keep,
        )

    # "window seats for 6 people" carries its own count. The stepper is the
    # fallback, not the other way round: the sentence is the more recent thing
    # the passenger said.
    if query.party:
        wanted = query.party
    else:
        try:
            wanted = max(1, min(settings.MAX_PARTY_SIZE, int(party)))
        except (TypeError, ValueError):
            wanted = 1

    seats = selectors.search_seats(flight, query, limit=wanted)
    if not seats:
        return _booking_response(
            request,
            flight,
            message=f'No free {describe(query)} on this flight.',
            ok=False,
            selected=keep,
        )

    found = describe(query)
    return _booking_response(
        request,
        flight,
        message=f'{found[0].upper()}{found[1:]}.',
        ok=True,
        selected=[seat.designation for seat in seats],
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

    prose = request.GET.get('q', '').strip()
    if prose:
        return _search_response(request, flight, prose, party, keep)

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
