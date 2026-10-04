from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0025_codeworkspace_owner_updated_index"),
    ]

    operations = [
        migrations.RenameIndex(model_name="apikey", old_name="api_apikey_user_revoked", new_name="api_session_user_rev"),
        migrations.RenameIndex(model_name="auditlog", old_name="api_auditlo_organiz_2c6f42_idx", new_name="api_audit_org_created"),
        migrations.RenameIndex(model_name="auditlog", old_name="api_auditlo_user_id_9d8b23_idx", new_name="api_audit_user_created"),
        migrations.RenameIndex(model_name="backgroundjob", old_name="api_backgro_status_53ef0d_idx", new_name="api_job_status_avail"),
        migrations.RenameIndex(model_name="backgroundjob", old_name="api_backgro_kind_1d4b0f_idx", new_name="api_job_kind_created"),
        migrations.RenameIndex(model_name="backgroundjob", old_name="api_backgr_idem_k_3e3e8b_idx", new_name="api_job_idem_kind"),
        migrations.RenameIndex(model_name="billingevent", old_name="api_billing_event_type_9c6a0b_idx", new_name="api_bill_evt_type_idx"),
        migrations.RenameIndex(model_name="emailverificationtoken", old_name="api_emailver_user_2a31f0_idx", new_name="api_emailve_user_id_0dafb9_idx"),
        migrations.RenameIndex(model_name="loginattempt", old_name="api_loginat_identif_1fdc3b_idx", new_name="api_login_ident_created"),
        migrations.RenameIndex(model_name="loginattempt", old_name="api_loginat_ip_addr_9c1d5b_idx", new_name="api_login_ip_created"),
        migrations.RenameIndex(model_name="organizationinvite", old_name="api_orginvi_organiz_1e3f55_idx", new_name="api_orginvite_org_email"),
        migrations.RenameIndex(model_name="organizationinvite", old_name="api_orginvi_token_2e4b72_idx", new_name="api_orginvite_token"),
        migrations.RenameIndex(model_name="productevent", old_name="api_product_name_9f2d11_idx", new_name="api_event_name_time"),
        migrations.RenameIndex(model_name="productevent", old_name="api_product_user_id_0cc22f_idx", new_name="api_event_user_name_time"),
        migrations.RenameIndex(model_name="securitysession", old_name="api_securit_user_id_0a7b20_idx", new_name="api_securit_user_id_dbe9e4_idx"),
        migrations.RenameIndex(model_name="securitysession", old_name="api_securit_user_id_72e6d1_idx", new_name="api_session_user_seen"),
        migrations.RenameIndex(model_name="usagerecord", old_name="api_usage_metric_period_idx", new_name="api_usage_metric_period"),
        migrations.AddField(model_name="codeworkspace", name="revision", field=models.PositiveBigIntegerField(default=1)),
    ]