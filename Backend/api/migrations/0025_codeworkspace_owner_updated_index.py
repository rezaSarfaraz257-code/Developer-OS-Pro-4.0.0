from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0024_remove_default_ide_main")]

    operations = [
        migrations.AddIndex(
            model_name="codeworkspace",
            index=models.Index(fields=["owner", "-updated_at"], name="api_codework_owner_updated"),
        ),
    ]
