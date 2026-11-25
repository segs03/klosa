import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Files live outside MEDIA_ROOT, so no URL can serve them. Writes need no file locking,
    which some filesystems (Android shared storage) do not support."""

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def _save(self, name, content):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        while True:
            full_path = self.path(name)
            os.makedirs(os.path.dirname(full_path), mode=0o700, exist_ok=True)
            try:
                fd = os.open(full_path, flags, 0o600)           # O_EXCL: never overwrite another file
            except FileExistsError:
                name = self.get_available_name(name)
                continue
            try:
                with os.fdopen(fd, "wb") as out:
                    for chunk in content.chunks():
                        out.write(chunk)
            except BaseException:
                try:
                    os.remove(full_path)                        # no half-written files left behind
                except OSError:
                    pass
                raise
            return str(name).replace("\\", "/")