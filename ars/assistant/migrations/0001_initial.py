"""Concept table, plus the vec0 virtual table that indexes it.

`vec0` is a virtual table outside the ORM's control, so it is created with
RunSQL and read through raw SQL. Its dimension is fixed at creation: changing
the embedding model means a new migration, not a config edit.
"""

from django.db import migrations, models

from assistant.providers import EMBEDDING_DIMENSIONS
from assistant.vocabulary import VEC_TABLE

CREATE_VEC_TABLE = f"""
CREATE VIRTUAL TABLE {VEC_TABLE} USING vec0(
    concept_id INTEGER PRIMARY KEY,
    embedding  FLOAT[{EMBEDDING_DIMENSIONS}]
);
"""

DROP_VEC_TABLE = f'DROP TABLE IF EXISTS {VEC_TABLE};'


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Concept',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('phrase', models.CharField(max_length=120, unique=True)),
                ('field', models.CharField(max_length=20)),
                ('value', models.CharField(max_length=20)),
            ],
            options={'ordering': ['field', 'phrase']},
        ),
        migrations.RunSQL(sql=CREATE_VEC_TABLE, reverse_sql=DROP_VEC_TABLE),
    ]
