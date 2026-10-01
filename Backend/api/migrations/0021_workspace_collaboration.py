from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("api", "0020_merge_ai_runtime_workspace")]
    operations = [
        migrations.CreateModel(
            name="WorkspaceCollaborationSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("client_id", models.CharField(max_length=96)),
                ("cursor", models.JSONField(blank=True, default=dict)),
                ("selection", models.JSONField(blank=True, default=dict)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="workspace_collaboration_sessions", to="auth.user")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="collaboration_sessions", to="api.codeworkspace")),
            ],
            options={"indexes":[models.Index(fields=["workspace","-last_seen_at"],name="api_ws_collab_last_seen_idx")],"constraints":[models.UniqueConstraint(fields=["workspace","user","client_id"],name="unique_workspace_collab_client")]},
        ),
        migrations.CreateModel(
            name="WorkspaceFileRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("path", models.CharField(max_length=500)),
                ("revision", models.PositiveBigIntegerField()),
                ("content", models.TextField()),
                ("client_id", models.CharField(blank=True, default="", max_length=96)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="auth.user")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="file_revisions", to="api.codeworkspace")),
            ],
            options={"indexes":[models.Index(fields=["workspace","path","-revision"],name="api_ws_file_rev_idx")],"constraints":[models.UniqueConstraint(fields=["workspace","path","revision"],name="unique_workspace_file_revision")]},
        ),
    ]
