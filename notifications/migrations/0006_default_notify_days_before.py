from django.db import migrations

# Only the global (family=None) built-in rows - a family's own custom
# event type keeps the field's plain default=0 (same-day), which is
# still the right default for something with no established lead-time
# convention of its own.
NOTIFY_DAYS_BEFORE = {
    "birthday": 1,
    "wedding": 7,
    "bar_mitzvah": 3,
    "bat_mitzvah": 3,
}


def _set_notify_days_before(apps, schema_editor):
    EventType = apps.get_model("notifications", "EventType")
    for code, days in NOTIFY_DAYS_BEFORE.items():
        EventType.objects.filter(family=None, code=code).update(notify_days_before=days)


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0005_yahrzeit_ancestors_config"),
    ]

    operations = [
        # Reversing this doesn't need to reconstruct the exact pre-
        # migration values (birthday/bar/bat mitzvah's own old 0s,
        # wedding's old 3) - it's just a cosmetic default, not data
        # worth precisely restoring on downgrade. A no-op reverse just
        # leaves whatever NOTIFY_DAYS_BEFORE set, which is harmless.
        migrations.RunPython(_set_notify_days_before, migrations.RunPython.noop),
    ]
