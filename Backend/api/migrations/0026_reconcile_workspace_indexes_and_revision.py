from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0025_codeworkspace_owner_updated_index"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_apikey_user_revoked" RENAME TO "api_session_user_rev"',
                reverse_sql='ALTER INDEX IF EXISTS "api_session_user_rev" RENAME TO "api_apikey_user_revoked"',
            )],
            state_operations=[migrations.RenameIndex(model_name="apikey", old_name="api_apikey_user_revoked", new_name="api_session_user_rev")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_auditlo_organiz_2c6f42_idx" RENAME TO "api_audit_org_created"',
                reverse_sql='ALTER INDEX IF EXISTS "api_audit_org_created" RENAME TO "api_auditlo_organiz_2c6f42_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="auditlog", old_name="api_auditlo_organiz_2c6f42_idx", new_name="api_audit_org_created")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_auditlo_user_id_9d8b23_idx" RENAME TO "api_audit_user_created"',
                reverse_sql='ALTER INDEX IF EXISTS "api_audit_user_created" RENAME TO "api_auditlo_user_id_9d8b23_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="auditlog", old_name="api_auditlo_user_id_9d8b23_idx", new_name="api_audit_user_created")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_backgro_status_53ef0d_idx" RENAME TO "api_job_status_avail"',
                reverse_sql='ALTER INDEX IF EXISTS "api_job_status_avail" RENAME TO "api_backgro_status_53ef0d_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="backgroundjob", old_name="api_backgro_status_53ef0d_idx", new_name="api_job_status_avail")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_backgro_kind_1d4b0f_idx" RENAME TO "api_job_kind_created"',
                reverse_sql='ALTER INDEX IF EXISTS "api_job_kind_created" RENAME TO "api_backgro_kind_1d4b0f_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="backgroundjob", old_name="api_backgro_kind_1d4b0f_idx", new_name="api_job_kind_created")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_backgr_idem_k_3e3e8b_idx" RENAME TO "api_job_idem_kind"',
                reverse_sql='ALTER INDEX IF EXISTS "api_job_idem_kind" RENAME TO "api_backgr_idem_k_3e3e8b_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="backgroundjob", old_name="api_backgr_idem_k_3e3e8b_idx", new_name="api_job_idem_kind")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_billing_event_type_9c6a0b_idx" RENAME TO "api_bill_evt_type_idx"',
                reverse_sql='ALTER INDEX IF EXISTS "api_bill_evt_type_idx" RENAME TO "api_billing_event_type_9c6a0b_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="billingevent", old_name="api_billing_event_type_9c6a0b_idx", new_name="api_bill_evt_type_idx")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_emailver_user_2a31f0_idx" RENAME TO "api_emailve_user_id_0dafb9_idx"',
                reverse_sql='ALTER INDEX IF EXISTS "api_emailve_user_id_0dafb9_idx" RENAME TO "api_emailver_user_2a31f0_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="emailverificationtoken", old_name="api_emailver_user_2a31f0_idx", new_name="api_emailve_user_id_0dafb9_idx")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_loginat_identif_1fdc3b_idx" RENAME TO "api_login_ident_created"',
                reverse_sql='ALTER INDEX IF EXISTS "api_login_ident_created" RENAME TO "api_loginat_identif_1fdc3b_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="loginattempt", old_name="api_loginat_identif_1fdc3b_idx", new_name="api_login_ident_created")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_loginat_ip_addr_9c1d5b_idx" RENAME TO "api_login_ip_created"',
                reverse_sql='ALTER INDEX IF EXISTS "api_login_ip_created" RENAME TO "api_loginat_ip_addr_9c1d5b_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="loginattempt", old_name="api_loginat_ip_addr_9c1d5b_idx", new_name="api_login_ip_created")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_orginvi_organiz_1e3f55_idx" RENAME TO "api_orginvite_org_email"',
                reverse_sql='ALTER INDEX IF EXISTS "api_orginvite_org_email" RENAME TO "api_orginvi_organiz_1e3f55_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="organizationinvite", old_name="api_orginvi_organiz_1e3f55_idx", new_name="api_orginvite_org_email")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_orginvi_token_2e4b72_idx" RENAME TO "api_orginvite_token"',
                reverse_sql='ALTER INDEX IF EXISTS "api_orginvite_token" RENAME TO "api_orginvi_token_2e4b72_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="organizationinvite", old_name="api_orginvi_token_2e4b72_idx", new_name="api_orginvite_token")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_product_name_9f2d11_idx" RENAME TO "api_event_name_time"',
                reverse_sql='ALTER INDEX IF EXISTS "api_event_name_time" RENAME TO "api_product_name_9f2d11_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="productevent", old_name="api_product_name_9f2d11_idx", new_name="api_event_name_time")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_product_user_id_0cc22f_idx" RENAME TO "api_event_user_name_time"',
                reverse_sql='ALTER INDEX IF EXISTS "api_event_user_name_time" RENAME TO "api_product_user_id_0cc22f_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="productevent", old_name="api_product_user_id_0cc22f_idx", new_name="api_event_user_name_time")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_securit_user_id_0a7b20_idx" RENAME TO "api_securit_user_id_dbe9e4_idx"',
                reverse_sql='ALTER INDEX IF EXISTS "api_securit_user_id_dbe9e4_idx" RENAME TO "api_securit_user_id_0a7b20_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="securitysession", old_name="api_securit_user_id_0a7b20_idx", new_name="api_securit_user_id_dbe9e4_idx")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql='ALTER INDEX IF EXISTS "api_securit_user_id_72e6d1_idx" RENAME TO "api_session_user_seen"',
                reverse_sql='ALTER INDEX IF EXISTS "api_session_user_seen" RENAME TO "api_securit_user_id_72e6d1_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="securitysession", old_name="api_securit_user_id_72e6d1_idx", new_name="api_session_user_seen")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql="""DO $$
BEGIN
    IF to_regclass('public.api_usage_metric_period_idx') IS NOT NULL
       AND to_regclass('public.api_usage_metric_period') IS NULL THEN
        ALTER INDEX "api_usage_metric_period_idx" RENAME TO "api_usage_metric_period";
    END IF;
END
$$;""",
                reverse_sql='ALTER INDEX IF EXISTS "api_usage_metric_period" RENAME TO "api_usage_metric_period_idx"',
            )],
            state_operations=[migrations.RenameIndex(model_name="usagerecord", old_name="api_usage_metric_period_idx", new_name="api_usage_metric_period")],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql="""DO $
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'api_codeworkspace'
          AND column_name = 'revision'
    ) THEN
        ALTER TABLE "api_codeworkspace" ADD COLUMN "revision" bigint DEFAULT 1 NOT NULL;
    END IF;
END
$;""",
                reverse_sql='ALTER TABLE "api_codeworkspace" DROP COLUMN IF EXISTS "revision"',
            )],
            state_operations=[migrations.AddField(model_name="codeworkspace", name="revision", field=models.PositiveBigIntegerField(default=1))],
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql='ALTER INDEX IF EXISTS "api_codework_owner_i_8f5a7c_idx" RENAME TO "api_codework_owner_updated"',
                    reverse_sql='ALTER INDEX IF EXISTS "api_codework_owner_updated" RENAME TO "api_codework_owner_i_8f5a7c_idx"',
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(model_name="codeworkspace", name="api_codework_owner_i_8f5a7c_idx"),
            ],
        ),
    ]