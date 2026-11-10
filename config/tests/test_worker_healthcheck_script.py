import subprocess
import time
from pathlib import Path

import pytest

from config import health

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "entrypoints" / "worker_healthcheck.sh"


def _write(
    directory: Path, name: str, *, age_seconds: float | None = None, content: str | None = None
) -> None:
    text = content if content is not None else str(int(time.time() - age_seconds))
    (directory / name).write_text(text + "\n")


def _run(directory: Path, mode: str, **env: str) -> int:
    return subprocess.run(
        ["sh", str(SCRIPT), mode],
        env={"PATH": "/usr/bin:/bin", "WORKER_HEALTH_DIR": str(directory), **env},
        capture_output=True,
    ).returncode


@pytest.mark.parametrize(
    ["mode", "files", "expected"],
    [
        ["live", {"worker-alive": {"age_seconds": 5}}, 0],
        ["live", {"worker-alive": {"age_seconds": 10_000}}, 1],
        ["live", {}, 1],
        ["live", {"worker-alive": {"content": "not-a-number"}}, 1],
        ["live", {"worker-alive": {"content": ""}}, 1],
        ["ready", {"worker-ready": {"age_seconds": 10_000}}, 0],
        ["ready", {}, 1],
    ],
    ids=[
        "live passes on a fresh file",
        "live fails on a stale file",
        "live fails on a missing file",
        "live fails on garbage content",
        "live fails on an empty file",
        "ready only needs the file to exist, however old",
        "ready fails on a missing file",
    ],
)
def test_probe_exit_code(tmp_path, mode, files, expected):
    for name, spec in files.items():
        _write(tmp_path, name, **spec)

    assert _run(tmp_path, mode) == expected


@pytest.mark.parametrize(
    ["age_offset", "expected"],
    [[-30, 0], [30, 1]],
    ids=["just inside the python constant passes", "just outside the python constant fails"],
)
def test_default_max_age_matches_the_python_constant(tmp_path, age_offset, expected):
    _write(tmp_path, "worker-alive", age_seconds=health.HEARTBEAT_MAX_AGE_SECONDS + age_offset)

    assert _run(tmp_path, "live") == expected


def test_max_age_can_be_overridden_by_env(tmp_path):
    _write(tmp_path, "worker-alive", age_seconds=120)

    assert _run(tmp_path, "live") == 0
    assert _run(tmp_path, "live", WORKER_HEALTHCHECK_MAX_AGE="60") == 1


def test_unknown_mode_is_a_usage_error(tmp_path):
    assert _run(tmp_path, "nonsense") == 2
