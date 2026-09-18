"""Dedicated WSGI app serving aggregated Prometheus metrics on port 9090.

Runs as a single-worker gunicorn process, separate from the main app
(see docker/entrypoints/_run_with_metrics.sh) - reuses gunicorn, already a
dependency, instead of adding a second web server package purely for this
sidecar. Must stay single-process: `MultiProcessCollector` itself is what
aggregates the *other* processes' per-pid `.db` files, so this process
isn't itself part of that multiprocess set.
"""

from config.observability.multiproc import init_multiprocess_dir

init_multiprocess_dir()

from prometheus_client import CollectorRegistry, make_wsgi_app, multiprocess  # noqa: E402

registry = CollectorRegistry()
multiprocess.MultiProcessCollector(registry)

application = make_wsgi_app(registry=registry)
