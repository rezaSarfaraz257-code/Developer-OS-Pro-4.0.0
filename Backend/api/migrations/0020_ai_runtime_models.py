from django.db import migrations, models
import django.db.models.deletion


def create_model_if_missing(apps, schema_editor, model_name):
    Model = apps.get_model("api", model_name)
    table = Model._meta.db_table
    if table not in schema_editor.connection.introspection.table_names():
        schema_editor.create_model(Model)


def create_ai_runtime_models(apps, schema_editor):
    # Create in dependency order. Older deployments may have model state without
    # physical tables, so each operation is idempotent.
    for name in ("Subscription", "CodeWorkspace", "FrameworkInstallation", "IDEExecution", "AIConversation", "AIMessage"):
        create_model_if_missing(apps, schema_editor, name)


def drop_ai_runtime_models(apps, schema_editor):
    for name in ("AIMessage", "AIConversation", "IDEExecution", "FrameworkInstallation", "CodeWorkspace", "Subscription"):
        Model = apps.get_model("api", name)
        table = Model._meta.db_table
        if table in schema_editor.connection.introspection.table_names():
            schema_editor.delete_model(Model)


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0019_workspace_revision"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    create_ai_runtime_models,
                    drop_ai_runtime_models,
                ),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="Subscription",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("plan", models.CharField(choices=[("free", "Free"), ("pro", "Pro"), ("team", "Team"), ("enterprise", "Enterprise")], default="free", max_length=20)),
                        ("status", models.CharField(choices=[("trialing", "Trialing"), ("active", "Active"), ("past_due", "Past due"), ("canceled", "Canceled")], default="active", max_length=20)),
                        ("provider_customer_id", models.CharField(blank=True, default="", max_length=180)),
                        ("provider_subscription_id", models.CharField(blank=True, default="", max_length=180)),
                        ("current_period_end", models.DateTimeField(blank=True, null=True)),
                        ("cancel_at_period_end", models.BooleanField(default=False)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription", to="auth.user")),
                    ],
                ),
                migrations.CreateModel(
                    name="CodeWorkspace",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("name", models.CharField(default="Untitled Workspace", max_length=160)),
                        ("files", models.JSONField(blank=True, default=dict)),
                        ("active_file", models.CharField(default="main.py", max_length=500)),
                        ("language", models.CharField(default="python", max_length=40)),
                        ("framework", models.CharField(blank=True, default="", max_length=80)),
                        ("runtime", models.CharField(blank=True, default="python", max_length=40)),
                        ("package_manager", models.CharField(blank=True, default="", max_length=30)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("revision", models.PositiveBigIntegerField(default=1)),
                        ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="code_workspaces", to="auth.user")),
                        ("project", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="code_workspaces", to="api.project")),
                    ],
                    options={
                        "indexes": [models.Index(fields=["owner", "-updated_at"], name="api_codework_owner_i_8f5a7c_idx")],
                    },
                ),
                migrations.CreateModel(
                    name="FrameworkInstallation",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("framework", models.CharField(max_length=80)),
                        ("package_manager", models.CharField(max_length=30)),
                        ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("success", "Success"), ("failed", "Failed")], default="queued", max_length=20)),
                        ("output", models.TextField(blank=True, default="")),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("finished_at", models.DateTimeField(blank=True, null=True)),
                        ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="installations", to="api.codeworkspace")),
                    ],
                ),
                migrations.CreateModel(
                    name="IDEExecution",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("command", models.CharField(max_length=2000)),
                        ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("success", "Success"), ("failed", "Failed"), ("timeout", "Timeout")], default="queued", max_length=20)),
                        ("stdout", models.TextField(blank=True, default="")),
                        ("stderr", models.TextField(blank=True, default="")),
                        ("exit_code", models.IntegerField(blank=True, null=True)),
                        ("duration_ms", models.IntegerField(blank=True, null=True)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("finished_at", models.DateTimeField(blank=True, null=True)),
                        ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="executions", to="api.codeworkspace")),
                    ],
                ),
                migrations.CreateModel(
                    name="AIConversation",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("title", models.CharField(default="New conversation", max_length=200)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ai_conversations", to="auth.user")),
                        ("project", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="ai_conversations", to="api.project")),
                    ],
                ),
                migrations.CreateModel(
                    name="AIMessage",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("role", models.CharField(choices=[("system", "System"), ("user", "User"), ("assistant", "Assistant"), ("tool", "Tool")], max_length=20)),
                        ("content", models.TextField()),
                        ("context", models.JSONField(blank=True, default=dict)),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="api.aiconversation")),
                    ],
                    options={"ordering": ["created_at"]},
                ),
            ],
        ),
    ]
