"""Thin views: parse, delegate to selectors/services, render.

See ARCHITECTURE.md section 6 for the URL and fragment design.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from reservations import selectors
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
        },
    )
