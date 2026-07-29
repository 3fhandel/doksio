from django.contrib.postgres.indexes import GinIndex, OpClass
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations
from django.db.models.functions import Upper

INDEX_NAME = "search_combined_text_trgm_gin"


def create_trigram_index(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
        ON search_documentsearchindex
        USING gin (UPPER(combined_text) gin_trgm_ops)
        """
    )


def drop_trigram_index(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}"
    )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("search", "0002_documentsearchindex_search_vector"),
    ]

    operations = [
        TrigramExtension(),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    create_trigram_index,
                    reverse_code=drop_trigram_index,
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="documentsearchindex",
                    index=GinIndex(
                        OpClass(Upper("combined_text"), name="gin_trgm_ops"),
                        name=INDEX_NAME,
                    ),
                ),
            ],
        ),
    ]
