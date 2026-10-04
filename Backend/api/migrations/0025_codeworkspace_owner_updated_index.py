from django.db import migrations, models


def add_index_if_missing(apps, schema_editor):
    model = apps.get_model("api", "CodeWorkspace")
    table = model._meta.db_table
    index_name = "api_codework_owner_updated"
    existing = {idx["name"] for idx in schema_editor.connection.introspection.get_constraints(schema_editor.connection.cursor(), table).values() if idx.get("index")}
    if index_name not in existing:
        schema_editor.add_index(model, models.Index(fields=["owner", "-updated_at"], name=index_name))


def remove_index_if_present(apps, schema_editor):
    model = apps.get_model("api", "CodeWorkspace")
    table = model._meta.db_table
    index_name = "api_codework_owner_updated"
    existing = {idx["name"] for idx in schema_editor.connection.introspection.get_constraints(schema_editor.connection.cursor(), table).values() if idx.get("index")}
    if index_name in existing:
        schema_editor.remove_index(model, models.Index(fields=["owner", "-updated_at"], name=index_name))


class Migration(migrations.Migration):
    dependencies = [("api", "0024_remove_default_ide_main")]
    operations = [migrations.RunPython(add_index_if_missing, remove_index_if_present)]
