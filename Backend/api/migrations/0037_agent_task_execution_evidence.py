from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("api", "0036_agent_task_specialist_role")]

    operations = [
        migrations.AddField(
            model_name="agenttask", name="execution_job",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                   related_name="agent_tasks", to="api.backgroundjob"),
        ),
        migrations.AddField(
            model_name="agenttask", name="execution_revision",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="agenttask", name="execution_profile",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="agenttask", name="execution_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="agenttask", name="execution_evidence",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="agenttask", name="execution_error",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
