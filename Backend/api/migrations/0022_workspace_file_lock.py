from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[("api","0021_workspace_collaboration")]
    operations=[
        migrations.CreateModel(
            name="WorkspaceFileLock",
            fields=[
                ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
                ("path",models.CharField(max_length=500)),
                ("client_id",models.CharField(max_length=96)),
                ("acquired_at",models.DateTimeField(auto_now_add=True)),
                ("expires_at",models.DateTimeField()),
                ("user",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to="auth.user")),
                ("workspace",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="file_locks",to="api.codeworkspace")),
            ],
            options={
                "indexes":[models.Index(fields=["workspace","path"],name="api_ws_file_lock_idx")],
                "constraints":[models.UniqueConstraint(fields=["workspace","path"],name="unique_workspace_file_lock")],
            },
        )
    ]
