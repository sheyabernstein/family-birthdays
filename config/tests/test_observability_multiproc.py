from pathlib import Path

import config.observability.multiproc as multiproc


def test_init_multiprocess_dir_creates_the_directory_and_sets_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setattr(multiproc, "_initialized", False)
    target = tmp_path / "prom"

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(target))
    result = multiproc.init_multiprocess_dir()

    assert result == str(target)
    assert target.is_dir()


def test_init_multiprocess_dir_is_idempotent(tmp_path, monkeypatch):
    """A second call shouldn't re-run the purge/mkdir logic - guarded by
    the module-level _initialized flag."""
    monkeypatch.setattr(multiproc, "_initialized", False)
    target = tmp_path / "prom"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(target))
    multiproc.init_multiprocess_dir()

    stale_file = target / "counter_123.db"
    stale_file.write_text("")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_WIPE", "1")

    multiproc.init_multiprocess_dir()

    assert stale_file.exists()


def test_init_multiprocess_dir_purges_stale_db_files_when_wipe_is_set(tmp_path, monkeypatch):
    monkeypatch.setattr(multiproc, "_initialized", False)
    target = tmp_path / "prom"
    target.mkdir()
    stale_file = target / "counter_123.db"
    stale_file.write_text("")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(target))
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_WIPE", "1")

    multiproc.init_multiprocess_dir()

    assert not stale_file.exists()


def test_init_multiprocess_dir_leaves_db_files_alone_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(multiproc, "_initialized", False)
    target = tmp_path / "prom"
    target.mkdir()
    stale_file = target / "counter_123.db"
    stale_file.write_text("")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(target))
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_WIPE", raising=False)

    multiproc.init_multiprocess_dir()

    assert stale_file.exists()


def test_purge_stale_db_files_tolerates_a_file_removed_by_another_worker(tmp_path, monkeypatch):
    stale_file = tmp_path / "counter_123.db"
    stale_file.write_text("")

    def _raise(self):
        raise OSError("already gone")

    monkeypatch.setattr(Path, "unlink", _raise)

    count = multiproc._purge_stale_db_files(tmp_path)

    assert count == 0


def test_register_atexit_mark_dead_registers_a_hook_that_never_raises(monkeypatch):
    """mark_process_dead can legitimately fail (e.g. the pid's own .db
    files are already gone) - the atexit hook must swallow that, since an
    atexit callback raising is itself a startup-log-worthy footgun."""
    import atexit

    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))

    def _boom(pid):
        raise RuntimeError("no such file")

    monkeypatch.setattr("prometheus_client.multiprocess.mark_process_dead", _boom)

    multiproc.register_atexit_mark_dead()

    assert len(registered) == 1
    registered[0]()  # must not raise
