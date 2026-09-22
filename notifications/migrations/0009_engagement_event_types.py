from django.db import migrations

# Mirrors Wedding/Anniversary (see notifications/migrations/0001_initial.py's
# own GLOBAL_EVENT_TYPES) - Engagement is the one-time announcement
# (recurs=False, computed only for the Hebrew year the engagement itself
# fell in), Engagement Anniversary is the yearly recurrence of it
# afterwards, the same relationship Wedding/Anniversary already have to
# marriage_date. Both default to notify_days_before=0 - unlike Wedding
# (sent a few days ahead of a real future date guests need lead time
# for), an engagement is normally recorded after it's already happened,
# so there's no "coming up" case to give lead time for.
GLOBAL_EVENT_TYPES = [
    {
        "code": "engagement",
        "name": "Engagement",
        "anchor": "engagement",
        "applies_to_union": True,
        "recurs": False,
        "notify_days_before": 0,
    },
    {
        "code": "engagement_anniversary",
        "name": "Engagement Anniversary",
        "anchor": "engagement",
        "applies_to_union": True,
        "recurs": True,
        "notify_days_before": 0,
        # Mirrors Anniversary's own default here - a yearly, secondary
        # reminder a family opts into rather than gets by default.
        "default_state": "muted",
    },
]


def seed_global_event_types(apps, schema_editor):
    EventType = apps.get_model("notifications", "EventType")
    for data in GLOBAL_EVENT_TYPES:
        EventType.objects.get_or_create(family=None, code=data["code"], defaults=data)


def unseed_global_event_types(apps, schema_editor):
    EventType = apps.get_model("notifications", "EventType")
    EventType.objects.filter(family=None, code__in=[d["code"] for d in GLOBAL_EVENT_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("family", "0003_union_engagement_date"),
        ("notifications", "0008_alter_eventtype_anchor_add_engagement"),
    ]

    operations = [
        migrations.RunPython(seed_global_event_types, unseed_global_event_types),
    ]
