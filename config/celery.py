import os

from celery import Celery
from celery.signals import setup_logging

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("family_birthdays")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@setup_logging.connect
def _use_structlog(**_kwargs) -> None:
    """Route every Celery log line through the structlog JSON pipeline.

    Celery's worker boot otherwise runs its own logging setup - hijacking the
    root logger and wrapping records in its text TaskFormatter, which is why
    worker/beat startup lines came out as plain text and our JSON logs got a
    `[timestamp: LEVEL/Process]` prefix stuck on the front. Connecting any
    receiver to this signal makes Celery skip that setup entirely. Importing
    Django settings above already imported config/logging_config.py (whose
    module-level code applied it), so by the time this fires the one shared
    JSON pipeline is the only logging config in play and everything -
    startup included - flows through it.
    """
