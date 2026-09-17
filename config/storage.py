from django.core.files.base import File
from whitenoise.storage import CompressedManifestStaticFilesStorage

# The event-type icons (notifications/static/notifications/img/event-icons/)
# are embedded directly into email HTML via a real <img src>, and
# Message.html_body is rendered once and persisted (see AGENTS.md's
# "Icons" bullet) - a hashed filename would 404 in every already-sent
# email the moment a later collectstatic run reassigned the hash. Every
# other static asset still gets the normal cache-busted hashed name.
STABLE_STATIC_PREFIXES: tuple[str] = ("notifications/img/event-icons/",)


class StableStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """CompressedManifestStaticFilesStorage that never hashes STABLE_STATIC_PREFIXES.

    Returning a falsy file_hash is a supported Django extension point -
    HashedFilesMixin.hashed_name() treats it as "leave the name alone"
    rather than inserting a hash segment.
    """

    def file_hash(self, name: str, content: File | None = None) -> str | None:
        if name.startswith(STABLE_STATIC_PREFIXES):
            return None
        return super().file_hash(name, content)
