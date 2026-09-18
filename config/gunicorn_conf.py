"""Gunicorn config file - reuses this app's own structured-logging setup.

Gunicorn builds its own plain-text handlers for "gunicorn.error"/
"gunicorn.access" before `config.wsgi` is imported. `logconfig_dict` is
what lets a config file override that: gunicorn applies it via
`logging.config.dictConfig()` as the last step of its own logging setup -
see `gunicorn.glogging.Logger.setup()`. This file can't import
`config.settings` (it runs before that's even importable), so it imports
the dict directly from `config.logging_config`.
"""

from gunicorn.arbiter import Arbiter
from gunicorn.workers.base import Worker

from config.logging_config import LOGGING_DICT_CONFIG

logconfig_dict = LOGGING_DICT_CONFIG


def post_fork(server: Arbiter, worker: Worker) -> None:
    """Sets up per-worker Prometheus multiprocess state right after gunicorn forks each worker.

    Deliberately not done via Django's own app-startup hooks (AppConfig.
    ready(), etc.) - those run *inside* each worker, after this hook, but
    prometheus_client's multiprocess mode needs PROMETHEUS_MULTIPROC_DIR
    set up (and the atexit hook registered) as early as possible in the
    child, before any metric is incremented. config.observability.multiproc
    has no Django dependency, so it's safe to import here even though this
    file can't import config.settings (see the module docstring above).
    """
    from config.observability.multiproc import init_multiprocess_dir, register_atexit_mark_dead

    init_multiprocess_dir()
    register_atexit_mark_dead()
