from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("api", "0027_referral_program")]
    operations = [
        migrations.AddField(model_name="referral", name="attributed_at", field=models.DateTimeField(auto_now_add=True)),
        migrations.AddField(model_name="referral", name="attribution_ip_hash", field=models.CharField(blank=True, default="", max_length=128)),
        migrations.AddField(model_name="referral", name="attribution_ua_hash", field=models.CharField(blank=True, default="", max_length=128)),
    ]
