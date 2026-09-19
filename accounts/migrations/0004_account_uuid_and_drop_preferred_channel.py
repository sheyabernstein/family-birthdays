import uuid

from django.db import migrations, models


def _backfill_uuids(apps, schema_editor):
    Account = apps.get_model("accounts", "Account")
    accounts = list(Account.objects.all())
    for account in accounts:
        account.uuid = uuid.uuid4()
    Account.objects.bulk_update(accounts, ["uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_alter_account_options"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="account",
            name="preferred_channel",
        ),
        # Added nullable/non-unique first, backfilled per-row, then locked
        # down - a plain AddField(unique=True, default=uuid.uuid4) fails at
        # makemigrations time against a table that already has rows (see
        # AGENTS.md's own note on this technique).
        migrations.AddField(
            model_name="account",
            name="uuid",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(_backfill_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="account",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
