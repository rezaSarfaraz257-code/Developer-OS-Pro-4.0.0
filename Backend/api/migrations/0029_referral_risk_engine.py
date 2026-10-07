from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("api", "0028_referral_integrity_hardening")]
    operations = [
        migrations.AddField(model_name="referral", name="risk_score", field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="referral", name="risk_reason", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="referral", name="last_checked_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(
            model_name="referral",
            name="status",
            field=models.CharField(
                choices=[("pending","Pending"),("rewarded","Rewarded"),("rejected","Rejected"),("review","Review")],
                default="pending", max_length=20,
            ),
        ),
    ]
