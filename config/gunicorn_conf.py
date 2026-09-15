"""Gunicorn config file - reuses this app's own structured-logging setup.

Gunicorn builds its own plain-text handlers for "gunicorn.error"/
"gunicorn.access" before `config.wsgi` is imported. `logconfig_dict` is
what lets a config file override that: gunicorn applies it via
`logging.config.dictConfig()` as the last step of its own logging setup -
see `gunicorn.glogging.Logger.setup()`. This file can't import
`config.settings` (it runs before that's even importable), so it imports
the dict directly from `config.logging_config`.
"""

from config.logging_config import LOGGING_DICT_CONFIG

logconfig_dict = LOGGING_DICT_CONFIG
