from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0037_agent_task_execution_evidence")]

    operations = [
        migrations.AddField(
            model_name="agenttask",
            name="acceptance_criteria",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
