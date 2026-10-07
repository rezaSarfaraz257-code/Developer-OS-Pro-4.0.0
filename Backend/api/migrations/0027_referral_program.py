from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0026_reconcile_workspace_indexes_and_revision"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReferralCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=32, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="referral_code", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Referral",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("rewarded", "Rewarded"), ("rejected", "Rejected")], default="pending", max_length=20)),
                ("qualified_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("code", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="referrals", to="api.referralcode")),
                ("referred", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="referral_received", to=settings.AUTH_USER_MODEL)),
                ("referrer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referrals_sent", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ReferralReward",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("milestone", models.PositiveIntegerField()),
                ("plan", models.CharField(default="pro", max_length=20)),
                ("duration_days", models.PositiveIntegerField(default=30)),
                ("starts_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("referral", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="rewards", to="api.referral")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="referral_rewards", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="referralreward",
            constraint=models.UniqueConstraint(fields=("user", "milestone"), name="unique_referral_reward_milestone"),
        ),
        migrations.AddIndex(
            model_name="referral",
            index=models.Index(fields=["referrer", "status"], name="api_referral_referrer_status"),
        ),
        migrations.AddIndex(
            model_name="referral",
            index=models.Index(fields=["code", "status"], name="api_referral_code_status"),
        ),
        migrations.AddIndex(
            model_name="referralreward",
            index=models.Index(fields=["user", "-expires_at"], name="api_ref_reward_user_expiry"),
        ),
    ]
