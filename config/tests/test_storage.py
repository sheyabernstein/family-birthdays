from config.storage import StableStaticFilesStorage


def test_file_hash_returns_none_for_a_stable_prefixed_path():
    storage = StableStaticFilesStorage()

    assert storage.file_hash("notifications/img/event-icons/birth.png", content=object()) is None


def test_file_hash_delegates_to_the_parent_when_name_is_none(monkeypatch):
    # save_manifest() calls file_hash(None, ...) to hash manifest.json
    # itself, not a real asset path - a real bug once, since name.startswith
    # crashes on None instead of falling through to the parent.
    storage = StableStaticFilesStorage()
    monkeypatch.setattr(
        "config.storage.CompressedManifestStaticFilesStorage.file_hash",
        lambda self, name, content=None: "manifest-hash",
    )

    assert storage.file_hash(None, content=object()) == "manifest-hash"


def test_file_hash_delegates_to_the_parent_for_everything_else(monkeypatch):
    storage = StableStaticFilesStorage()
    monkeypatch.setattr(
        "config.storage.CompressedManifestStaticFilesStorage.file_hash",
        lambda self, name, content=None: "parent-hash",
    )

    assert storage.file_hash("css/app.css", content=object()) == "parent-hash"
