from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0035_agent_run_task_graph"),
    ]

    operations = [
        migrations.AddField(
            model_name="agenttask",
            name="agent_role",
            field=models.CharField(
                choices=[
                    ("product_manager", "Product Manager"),
                    ("architect", "Software Architect"),
                    ("frontend", "Frontend Engineer"),
                    ("backend", "Backend Engineer"),
                    ("database", "Database Engineer"),
                    ("ui_ux", "UI/UX Designer"),
                    ("testing", "Test Engineer"),
                    ("security", "Security Engineer"),
                    ("devops", "DevOps Engineer"),
                    ("debugger", "Debugger"),
                    ("code_reviewer", "Code Reviewer"),
                    ("documentation", "Technical Writer"),
                ],
                default="architect",
                max_length=24,
            ),
        ),
    ]
