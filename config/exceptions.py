class FamilyBirthdaysError(Exception):
    """Base class for this app's own deliberate, already-handled domain errors.

    Not a catch-all for anything that happens to be raised in app code -
    only for a failure that's already logged and handled as a known
    outcome (see notifications.sms.SmsUnrecoverableError). Sentry
    integration (config/observability/sentry.py) checks against this base
    class specifically to skip double-reporting these - a genuinely
    unexpected bug should never subclass this.
    """
