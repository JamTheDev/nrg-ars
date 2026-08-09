from __future__ import annotations

from django.apps import AppConfig
from django.db.backends.signals import connection_created


def load_sqlite_vec(sender, connection, **kwargs) -> None:
    """Load the sqlite-vec extension into every new SQLite connection.

    Django has no built-in hook for SQLite extensions, so this rides the
    connection_created signal. Requires a Python built with extension loading
    enabled -- see ARCHITECTURE.md section 7.
    """
    if connection.vendor != 'sqlite':
        return

    import sqlite_vec

    connection.connection.enable_load_extension(True)
    sqlite_vec.load(connection.connection)
    connection.connection.enable_load_extension(False)


class AssistantConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'assistant'

    def ready(self) -> None:
        connection_created.connect(load_sqlite_vec)
