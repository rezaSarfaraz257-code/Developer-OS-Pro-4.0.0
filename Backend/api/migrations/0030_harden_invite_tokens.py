from django.db import migrations, models
import hashlib


def backfill_invite_hashes(apps, schema_editor):
    for model_name in ("ProjectInvite", "OrganizationInvite"):
        Model = apps.get_model("api", model_name)
        for invite in Model.objects.exclude(token__isnull=True).exclude(token="").iterator():
            invite.token_hash = hashlib.sha256(invite.token.encode("utf-8")).hexdigest()
            invite.save(update_fields=["token_hash"])


def clear_legacy_tokens(apps, schema_editor):
    for model_name in ("ProjectInvite", "OrganizationInvite"):
        Model = apps.get_model("api", model_name)
        Model.objects.filter(token__isnull=False).update(token=None)


class Migration(migrations.Migration):
    dependencies = [("api", "0029_referral_risk_engine")]

    operations = [
        migrations.AddField(
            model_name="projectinvite",
            name="token_hash",
            field=models.CharField(max_length=64, null=True, blank=True, unique=True),
        ),
        migrations.AddField(
            model_name="organizationinvite",
            name="token_hash",
            field=models.CharField(max_length=64, null=True, blank=True, unique=True),
        ),
        migrations.RunPython(backfill_invite_hashes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="projectinvite",
            name="token",
            field=models.CharField(max_length=96, null=True, blank=True, unique=True),
        ),
        migrations.AlterField(
            model_name="organizationinvite",
            name="token",
            field=models.CharField(max_length=128, null=True, blank=True, unique=True),
        ),
        migrations.RunPython(clear_legacy_tokens, migrations.RunPython.noop),
    ]
