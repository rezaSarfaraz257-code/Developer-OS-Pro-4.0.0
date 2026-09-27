from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("api", "0015_ide_platform")]
    operations = [
        migrations.AddField(
            model_name="subscription", name="cancel_at_period_end", field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="BillingEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.CharField(max_length=255, unique=True)),
                ("event_type", models.CharField(max_length=120)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"indexes": [models.Index(fields=["event_type", "-processed_at"], name="api_billing_event_type_9c6a0b_idx")]},
        ),
        migrations.CreateModel(
            name="UsageRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period", models.DateField()),
                ("metric", models.CharField(max_length=80)),
                ("quantity", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="usage_records", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "period"], name="api_usage_r_user_id_3d6fcb_idx"),
                    models.Index(fields=["metric", "period"], name="api_usage_r_metric_1c7b4d_idx"),
                ],
                "constraints": [models.UniqueConstraint(fields=("user", "period", "metric"), name="unique_user_usage_period_metric")],
            },
        ),
        migrations.CreateModel(
            name="OrganizationInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("role", models.CharField(choices=[("admin", "Admin"), ("developer", "Developer"), ("viewer", "Viewer")], default="developer", max_length=20)),
                ("token", models.CharField(max_length=128, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("inviter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="organization_invites_sent", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invites", to="api.organization")),
            ],
            options={"indexes": [models.Index(fields=["organization", "email"], name="api_orginvi_organiz_1e3f55_idx"), models.Index(fields=["token"], name="api_orginvi_token_2e4b72_idx")]},
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(max_length=120)),
                ("target_type", models.CharField(blank=True, default="", max_length=80)),
                ("target_id", models.CharField(blank=True, default="", max_length=120)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to="api.organization")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["organization", "-created_at"], name="api_auditlo_organiz_2c6f42_idx"), models.Index(fields=["user", "-created_at"], name="api_auditlo_user_id_9d8b23_idx")]},
        ),
    ]
