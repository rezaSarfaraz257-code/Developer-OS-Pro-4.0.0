# Generated for Developer OS usage metering recovery.
# This migration is intentionally idempotent because older deployments may have
# applied the model state without creating the table.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_usage_record_if_missing(apps, schema_editor):
    UsageRecord = apps.get_model("api", "UsageRecord")
    table = UsageRecord._meta.db_table
    existing = set(schema_editor.connection.introspection.table_names())
    if table not in existing:
        schema_editor.create_model(UsageRecord)


def drop_usage_record_if_present(apps, schema_editor):
    UsageRecord = apps.get_model("api", "UsageRecord")
    table = UsageRecord._meta.db_table
    existing = set(schema_editor.connection.introspection.table_names())
    if table in existing:
        schema_editor.delete_model(UsageRecord)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    create_usage_record_if_missing,
                    drop_usage_record_if_present,
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="UsageRecord",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                auto_created=True,
                                primary_key=True,
                                serialize=False,
                                verbose_name="ID",
                            ),
                        ),
                        (
                            "period",
                            models.DateField(),
                        ),
                        (
                            "metric",
                            models.CharField(max_length=80),
                        ),
                        (
                            "quantity",
                            models.PositiveIntegerField(default=0),
                        ),
                        (
                            "updated_at",
                            models.DateTimeField(auto_now=True),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="usage_records",
                                to=settings.AUTH_USER_MODEL,
                            ),
                        ),
                    ],
                    options={
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("user", "period", "metric"),
                                name="unique_user_usage_period_metric",
                            ),
                        ],
                        "indexes": [
                            models.Index(fields=["user", "period"], name="api_usage_user_period_idx"),
                            models.Index(fields=["metric", "period"], name="api_usage_metric_period_idx"),
                        ],
                    },
                ),
            ],
        ),
    ]
