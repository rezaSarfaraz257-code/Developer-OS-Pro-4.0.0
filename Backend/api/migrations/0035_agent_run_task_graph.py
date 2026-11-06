from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0034_public_project_visibility"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("goal", models.TextField(max_length=12000)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("planning", "Planning"), ("awaiting_approval", "Awaiting approval"), ("approved", "Approved"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("canceled", "Canceled")], db_index=True, default="queued", max_length=24)),
                ("plan", models.JSONField(blank=True, default=dict)),
                ("error", models.CharField(blank=True, default="", max_length=500)),
                ("idempotency_key", models.CharField(max_length=120)),
                ("workspace_revision", models.PositiveBigIntegerField()),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("conversation", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="agent_runs", to="api.aiconversation")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_runs", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_runs", to="api.codeworkspace")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["owner", "-created_at"], name="api_agent_run_owner_idx"),
                    models.Index(fields=["status", "created_at"], name="api_agent_run_status_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("owner", "idempotency_key"), name="unique_agent_run_owner_key"),
                ],
            },
        ),
        migrations.CreateModel(
            name="AgentTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=64)),
                ("title", models.CharField(max_length=180)),
                ("description", models.TextField(blank=True, default="", max_length=2000)),
                ("status", models.CharField(choices=[("blocked", "Blocked"), ("ready", "Ready"), ("running", "Running"), ("awaiting_approval", "Awaiting approval"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("canceled", "Canceled")], default="blocked", max_length=24)),
                ("sequence", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("depends_on", models.ManyToManyField(blank=True, related_name="dependents", to="api.agenttask")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tasks", to="api.agentrun")),
            ],
            options={
                "indexes": [
                    models.Index(fields=["run", "sequence"], name="api_agent_task_order_idx"),
                    models.Index(fields=["run", "status"], name="api_agent_task_status_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("run", "key"), name="unique_agent_task_run_key"),
                ],
            },
        ),
    ]
