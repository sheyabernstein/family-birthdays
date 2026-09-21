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

# What each of these actually was before this migration - used to revert
# cleanly, not just zero everything out (wedding's own 3-day lead time
# predates this migration, seeded by 0001_initial).
PREVIOUS_NOTIFY_DAYS_BEFORE = {
    "birthday": 0,
    "wedding": 3,
    "bar_mitzvah": 0,
    "bat_mitzvah": 0,
}


def _set_notify_days_before(apps, schema_editor):
    EventType = apps.get_model("notifications", "EventType")
    for code, days in NOTIFY_DAYS_BEFORE.items():
        EventType.objects.filter(family=None, code=code).update(notify_days_before=days)


def _revert_notify_days_before(apps, schema_editor):
    EventType = apps.get_model("notifications", "EventType")
    for code, days in PREVIOUS_NOTIFY_DAYS_BEFORE.items():
        EventType.objects.filter(family=None, code=code).update(notify_days_before=days)


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0005_yahrzeit_ancestors_config"),
    ]

    operations = [
        migrations.RunPython(_set_notify_days_before, _revert_notify_days_before),
    ]
