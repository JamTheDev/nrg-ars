from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST


def htmx_demo(request: HttpRequest) -> HttpResponse:
    return render(request, 'demo.html')


@require_POST
def htmx_demo_ping(request: HttpRequest) -> HttpResponse:
    # request.htmx is provided by django_htmx.middleware.HtmxMiddleware.
    source = 'htmx' if request.htmx else 'a plain request'
    return HttpResponse(f'<p>pong &mdash; served to {source}.</p>')
