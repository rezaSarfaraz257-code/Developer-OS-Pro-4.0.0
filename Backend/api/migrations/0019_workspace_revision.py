from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0018_production_reliability")]

    operations = [
        migrations.AddField(
            model_name="codeworkspace",
            name="revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
    ]
