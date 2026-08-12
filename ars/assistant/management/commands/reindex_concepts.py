"""Rebuild the concept vector index.

Run after changing CONCEPTS, or after changing the embedding model -- the
vec0 table's dimension is fixed at creation, so a model change is a migration
followed by this command.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from assistant import vocabulary
from assistant.providers import ProviderUnavailable


class Command(BaseCommand):
    help = 'Embed the curated concept vocabulary into the vec0 index.'

    def handle(self, *args, **options) -> None:
        try:
            count = vocabulary.reindex()
        except ProviderUnavailable as exc:
            raise CommandError(f'Ollama is not reachable: {exc}') from exc

        self.stdout.write(self.style.SUCCESS(f'{count} concepts indexed.'))
