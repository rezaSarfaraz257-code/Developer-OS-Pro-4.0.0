from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[("api","0022_workspace_file_lock")]
    operations=[migrations.CreateModel(name="WorkspaceCRDTOperation",fields=[
        ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
        ("path",models.CharField(max_length=500)),("operation_id",models.CharField(max_length=128,unique=True)),
        ("actor_id",models.CharField(max_length=128)),("lamport",models.PositiveBigIntegerField(default=0)),
        ("kind",models.CharField(max_length=16)),("position",models.PositiveIntegerField(default=0)),
        ("delete_count",models.PositiveIntegerField(default=0)),("text",models.TextField(blank=True,default="")),
        ("update_blob",models.TextField(blank=True,default="")),("created_at",models.DateTimeField(auto_now_add=True)),
        ("author",models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,to="auth.user")),
        ("workspace",models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name="crdt_operations",to="api.codeworkspace"))],
        options={"indexes":[models.Index(fields=["workspace","path","lamport"],name="api_crdt_path_lam_idx"),models.Index(fields=["workspace","path","created_at"],name="api_crdt_path_created_idx")]}
    )]