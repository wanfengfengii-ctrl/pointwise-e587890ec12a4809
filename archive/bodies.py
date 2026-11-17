import hashlib
import os
import re
import tempfile
from pathlib import Path
from dataclasses import dataclass


class ObjectIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class BodyRef:
    digest: str
    size: int


class BodyStore:
    def __init__(self, root):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.staging = self.root / "staging"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

    def object_path(self, ref):
        if not re.fullmatch(r"[0-9a-f]{64}", ref.digest) or type(ref.size) is not int or ref.size < 0:
            raise ObjectIntegrityError("invalid object reference")
        return self.objects / ref.digest

    def _read(self, path, ref):
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise ObjectIntegrityError("missing or unreadable source object") from exc
        if len(data) != ref.size or hashlib.sha256(data).hexdigest() != ref.digest:
            raise ObjectIntegrityError("source object length or digest mismatch")
        return data

    def stage_raw(self, raw, directory):
        ref = BodyRef(hashlib.sha256(raw).hexdigest(), len(raw))
        descriptor, name = tempfile.mkstemp(prefix="raw-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        self._read(name, ref)
        return ref, Path(name)

    def install(self, candidate, ref):
        self._read(candidate, ref)
        destination = self.object_path(ref)
        try:
            os.link(candidate, destination)
        except FileExistsError:
            self.read_verified(ref)
        directory_fd = os.open(self.objects, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return ref

    def read_verified(self, ref):
        return self._read(self.object_path(ref), ref)
