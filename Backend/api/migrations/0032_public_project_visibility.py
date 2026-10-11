from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0031_extended_user_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="is_public",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
