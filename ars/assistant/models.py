"""The curated concept vocabulary.

Rows here are authored, not derived: they change when the vocabulary changes,
never when someone books a seat. That is what makes an index over them
legitimate, and an index over seats not -- see ARCHITECTURE.md section 7.
"""

from __future__ import annotations

from django.db import models


class Concept(models.Model):
    """One phrase, and the SeatQuery field value it means."""

    phrase = models.CharField(max_length=120, unique=True)
    field = models.CharField(max_length=20)  # 'position', 'min_row', 'max_row'
    value = models.CharField(max_length=20)  # 'window', '21', ...

    class Meta:
        ordering = ['field', 'phrase']

    def __str__(self) -> str:
        return f'{self.phrase} -> {self.field}={self.value}'
