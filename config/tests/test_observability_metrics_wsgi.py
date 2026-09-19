"""metrics_wsgi.py runs init_multiprocess_dir() and builds its WSGI app at
import time, off whatever PROMETHEUS_MULTIPROC_DIR is set to at that
moment - so the env var has to be pointed at a throwaway directory before
the module is first imported anywhere in the test session, not inside an
individual test (tmp_path isn't available yet at this point - no test has
started running).

Always overwritten, not os.environ.setdefault() - the real value is
already set to /tmp/prom_multiproc under a container (see the
Dockerfile), and setdefault() would silently keep that instead of
pointing this test at its own isolated directory.
"""

import atexit
import os
import shutil
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="prom_multiproc_test_")
atexit.register(shutil.rmtree, _tmp_dir, ignore_errors=True)
os.environ["PROMETHEUS_MULTIPROC_DIR"] = _tmp_dir

from config.observability.metrics_wsgi import application  # noqa: E402


def test_metrics_wsgi_app_serves_prometheus_text_format():
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "9090",
        "wsgi.input": "",
        "wsgi.errors": "",
        "wsgi.version": (1, 0),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "wsgi.url_scheme": "http",
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(application(environ, start_response))

    assert captured["status"] == "200 OK"
    assert "text/plain" in captured["headers"]["Content-Type"]
    assert isinstance(body, bytes)
