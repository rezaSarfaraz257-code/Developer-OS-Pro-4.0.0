from django.db import migrations

STARTER = "# Developer OS Web IDE\nprint('Hello, Developer OS')\n"


def remove_only_unmodified_starter(apps, schema_editor):
    CodeWorkspace = apps.get_model("api", "CodeWorkspace")
    for ws in CodeWorkspace.objects.all().iterator():
        files = ws.files if isinstance(ws.files, dict) else {}
        if files.get("main.py") == STARTER:
            updated = dict(files)
            del updated["main.py"]
            ws.files = updated
            if ws.active_file == "main.py":
                ws.active_file = next(iter(updated), "")
            ws.save(update_fields=["files", "active_file"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("api", "0023_workspace_crdt_operation")]
    operations = [migrations.RunPython(remove_only_unmodified_starter, noop)]
