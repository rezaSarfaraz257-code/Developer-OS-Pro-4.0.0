# Generated for durable background-worker readiness reporting.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0032_community_feed"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkerHeartbeat",
            fields=[
                ("name", models.CharField(max_length=100, primary_key=True, serialize=False)),
                ("worker_id", models.CharField(max_length=160)),
                ("last_seen_at", models.DateTimeField()),
            ],
        ),
    ]
