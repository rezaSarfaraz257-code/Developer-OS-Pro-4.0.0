from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import api.fields


def mark_existing_profiles_verified(apps, schema_editor):
    UserProfile = apps.get_model("api", "UserProfile")
    UserProfile.objects.filter(user__email__isnull=False).exclude(user__email="").update(email_verified=True)


class Migration(migrations.Migration):
    dependencies = [("api", "0016_saas_maturity")]
    operations = [
        migrations.RunPython(mark_existing_profiles_verified, migrations.RunPython.noop),
        migrations.AddField(model_name="userprofile", name="email_verified", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="userprofile", name="email_verified_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.CreateModel(
            name="EmailVerificationToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(max_length=128, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="email_verification_tokens", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["user", "expires_at"], name="api_emailver_user_2a31f0_idx")]},
        ),
        migrations.CreateModel(
            name="SecuritySession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("jti", models.CharField(max_length=255, unique=True)),
                ("device_name", models.CharField(blank=True, default="", max_length=200)),
                ("user_agent", models.TextField(blank=True, default="")),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="security_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["user", "revoked_at"], name="api_securit_user_id_0a7b20_idx"), models.Index(fields=["user", "-last_seen_at"], name="api_securit_user_id_72e6d1_idx")]},
        ),
        migrations.CreateModel(
            name="MFADevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("secret", api.fields.EncryptedTextField()),
                ("enabled", models.BooleanField(default=False)),
                ("backup_code_hashes", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="mfa_device", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="LoginAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifier", models.CharField(max_length=254)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True, default="")),
                ("success", models.BooleanField(default=False)),
                ("reason", models.CharField(blank=True, default="", max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="login_attempts", to=settings.AUTH_USER_MODEL)),
            ],
            options={"indexes": [models.Index(fields=["identifier", "-created_at"], name="api_loginat_identif_1fdc3b_idx"), models.Index(fields=["ip_address", "-created_at"], name="api_loginat_ip_addr_9c1d5b_idx")]},
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("product_updates", models.BooleanField(default=True)), ("security_alerts", models.BooleanField(default=True)), ("team_activity", models.BooleanField(default=True)), ("billing", models.BooleanField(default=True)), ("marketing", models.BooleanField(default=False)), ("email_enabled", models.BooleanField(default=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="notification_preferences", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="OrganizationSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan", models.CharField(choices=[("free", "Free"), ("pro", "Pro"), ("team", "Team"), ("enterprise", "Enterprise")], default="free", max_length=20)),
                ("status", models.CharField(choices=[("trialing", "Trialing"), ("active", "Active"), ("past_due", "Past due"), ("canceled", "Canceled")], default="active", max_length=20)),
                ("provider_customer_id", models.CharField(blank=True, default="", max_length=180)), ("provider_subscription_id", models.CharField(blank=True, default="", max_length=180)), ("price_id", models.CharField(blank=True, default="", max_length=180)), ("quantity", models.PositiveIntegerField(default=1)), ("current_period_end", models.DateTimeField(blank=True, null=True)), ("cancel_at_period_end", models.BooleanField(default=False)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription_record", to="api.organization")),
            ],
        ),
        migrations.CreateModel(
            name="BillingInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("provider_invoice_id", models.CharField(max_length=180, unique=True)), ("number", models.CharField(blank=True, default="", max_length=120)), ("status", models.CharField(choices=[("draft", "Draft"), ("open", "Open"), ("paid", "Paid"), ("void", "Void"), ("uncollectible", "Uncollectible")], default="open", max_length=24)), ("currency", models.CharField(default="usd", max_length=8)), ("subtotal", models.BigIntegerField(default=0)), ("tax", models.BigIntegerField(default=0)), ("total", models.BigIntegerField(default=0)), ("amount_due", models.BigIntegerField(default=0)), ("hosted_url", models.URLField(blank=True, default="")), ("invoice_pdf", models.URLField(blank=True, default="")), ("due_at", models.DateTimeField(blank=True, null=True)), ("paid_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="invoices", to="api.organization")), ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="billing_invoices", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="PaymentAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("provider_payment_id", models.CharField(blank=True, default="", max_length=180)), ("status", models.CharField(choices=[("pending", "Pending"), ("succeeded", "Succeeded"), ("failed", "Failed")], default="pending", max_length=20)), ("amount", models.BigIntegerField(default=0)), ("failure_code", models.CharField(blank=True, default="", max_length=120)), ("failure_message", models.TextField(blank=True, default="")), ("attempted_at", models.DateTimeField(auto_now_add=True)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_attempts", to="api.billinginvoice")),
            ],
        ),
        migrations.CreateModel(
            name="BillingCredit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("amount", models.BigIntegerField(default=0)), ("currency", models.CharField(default="usd", max_length=8)), ("reason", models.CharField(max_length=240)), ("applied_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="billing_credits", to="api.organization")), ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="billing_credits", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="BackgroundJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("kind", models.CharField(max_length=100)), ("payload", models.JSONField(blank=True, default=dict)), ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("canceled", "Canceled")], default="queued", max_length=20)), ("attempts", models.PositiveIntegerField(default=0)), ("max_attempts", models.PositiveIntegerField(default=3)), ("available_at", models.DateTimeField(default=django.utils.timezone.now)), ("locked_at", models.DateTimeField(blank=True, null=True)), ("locked_by", models.CharField(blank=True, default="", max_length=120)), ("result", models.JSONField(blank=True, default=dict)), ("error", models.TextField(blank=True, default="")), ("created_at", models.DateTimeField(auto_now_add=True)), ("finished_at", models.DateTimeField(blank=True, null=True)),
            ], options={"indexes": [models.Index(fields=["status", "available_at"], name="api_backgro_status_53ef0d_idx"), models.Index(fields=["kind", "-created_at"], name="api_backgro_kind_1d4b0f_idx")]},
        ),
        migrations.CreateModel(
            name="DataExportRequest",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("ready", "Ready"), ("failed", "Failed"), ("expired", "Expired")], default="queued", max_length=20)), ("file_path", models.CharField(blank=True, default="", max_length=500)), ("expires_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("completed_at", models.DateTimeField(blank=True, null=True)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="data_export_requests", to=settings.AUTH_USER_MODEL))],
        ),
        migrations.CreateModel(
            name="AccountDeletionRequest",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("token_hash", models.CharField(max_length=128, unique=True)), ("status", models.CharField(choices=[("pending", "Pending"), ("confirmed", "Confirmed"), ("completed", "Completed"), ("canceled", "Canceled")], default="pending", max_length=20)), ("scheduled_for", models.DateTimeField()), ("created_at", models.DateTimeField(auto_now_add=True)), ("confirmed_at", models.DateTimeField(blank=True, null=True)), ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="deletion_request", to=settings.AUTH_USER_MODEL))],
        ),
        migrations.CreateModel(
            name="SupportTicket",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("subject", models.CharField(max_length=240)), ("body", models.TextField()), ("status", models.CharField(choices=[("open", "Open"), ("pending", "Pending"), ("resolved", "Resolved"), ("closed", "Closed")], default="open", max_length=20)), ("priority", models.CharField(choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")], default="normal", max_length=20)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_support_tickets", to=settings.AUTH_USER_MODEL)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_tickets", to=settings.AUTH_USER_MODEL))],
        ),
        migrations.CreateModel(
            name="ProductEvent",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.CharField(max_length=120)), ("properties", models.JSONField(blank=True, default=dict)), ("occurred_at", models.DateTimeField(auto_now_add=True)), ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="product_events", to="api.organization")), ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="product_events", to=settings.AUTH_USER_MODEL))],
            options={"indexes": [models.Index(fields=["name", "-occurred_at"], name="api_product_name_9f2d11_idx"), models.Index(fields=["user", "name", "-occurred_at"], name="api_product_user_id_0cc22f_idx")]},
        ),
        migrations.CreateModel(
            name="Incident",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("title", models.CharField(max_length=240)), ("status", models.CharField(choices=[("investigating", "Investigating"), ("identified", "Identified"), ("monitoring", "Monitoring"), ("resolved", "Resolved")], default="investigating", max_length=20)), ("severity", models.CharField(choices=[("sev1", "SEV1"), ("sev2", "SEV2"), ("sev3", "SEV3"), ("sev4", "SEV4")], default="sev3", max_length=10)), ("summary", models.TextField(blank=True, default="")), ("started_at", models.DateTimeField(default=django.utils.timezone.now)), ("resolved_at", models.DateTimeField(blank=True, null=True)), ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="incidents_created", to=settings.AUTH_USER_MODEL))],
        ),
        migrations.CreateModel(
            name="ObjectStorageFile",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("key", models.CharField(max_length=500, unique=True)), ("bucket", models.CharField(blank=True, default="", max_length=160)), ("content_type", models.CharField(blank=True, default="application/octet-stream", max_length=180)), ("size", models.BigIntegerField(default=0)), ("checksum", models.CharField(blank=True, default="", max_length=128)), ("provider", models.CharField(default="local", max_length=40)), ("created_at", models.DateTimeField(auto_now_add=True)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="object_files", to=settings.AUTH_USER_MODEL))],
        ),
        migrations.CreateModel(
            name="OrganizationRolePermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(max_length=40)),
                ("permissions", models.JSONField(blank=True, default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="role_permissions", to="api.organization")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("organization", "role"), name="unique_org_role_permission")]},
        ),
    ]
