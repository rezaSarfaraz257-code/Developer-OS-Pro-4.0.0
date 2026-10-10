from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0030_harden_invite_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="country",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="city",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="job_title",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="skills",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="timezone",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
