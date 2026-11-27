from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0038_agent_task_acceptance_criteria"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_revision", models.PositiveBigIntegerField()),
                ("applied_revision", models.PositiveBigIntegerField()),
                ("files", models.JSONField()),
                ("active_file", models.CharField(blank=True, default="", max_length=500)),
                ("sha256", models.CharField(max_length=64)),
                ("restored_at", models.DateTimeField(blank=True, null=True)),
                ("restored_revision", models.PositiveBigIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workspace_snapshots", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="snapshots", to="api.codeworkspace")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["workspace", "-created_at"], name="api_ws_snapshot_created"),
                    models.Index(fields=["owner", "workspace"], name="api_ws_snapshot_owner"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("workspace", "source_revision", "applied_revision"), name="unique_workspace_revision_snapshot"),
                ],
            },
        ),
    ]
