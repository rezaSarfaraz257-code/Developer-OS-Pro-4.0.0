from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0030_harden_invite_tokens")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="is_public",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
