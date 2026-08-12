"""Static URLs that change when the file does.

Django's dev server sends static files with a Last-Modified header and no
Cache-Control, so browsers fall back to heuristic freshness and can serve a
stale script for minutes. Editing a template and reloading then gives you new
markup driving old JavaScript, which looks exactly like a broken button.

In DEBUG the URL carries the file's modification time, so a changed file is a
changed URL. In production the URL is left alone -- cache-busting there is
collectstatic's job.
"""

from __future__ import annotations

import os

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def versioned_static(path: str) -> str:
    url = static(path)
    if not settings.DEBUG:
        return url

    absolute = finders.find(path)
    if not absolute:
        return url

    try:
        stamp = int(os.path.getmtime(absolute))
    except OSError:
        return url
    return f'{url}?v={stamp}'
