from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0017_mature_saas_control_plane")]

    operations = [
        migrations.AddField(model_name="backgroundjob", name="dead_lettered_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="backgroundjob", name="idempotency_key", field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="backgroundjob", name="last_error_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddIndex(model_name="backgroundjob", index=models.Index(fields=["idempotency_key", "kind"], name="api_backgr_idem_k_3e3e8b_idx")),
        migrations.AddConstraint(model_name="backgroundjob", constraint=models.UniqueConstraint(condition=~models.Q(idempotency_key=""), fields=("kind", "idempotency_key"), name="unique_job_kind_idempotency")),
    ]
