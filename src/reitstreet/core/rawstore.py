"""Immutable, content-addressed raw object store.

Every byte we ever receive (SEC JSON, EDGAR index pages, exhibits, supplemental PDFs,
emails) is stored exactly as received, gzip-compressed, at

    <root>/sha256/<ab>/<cd>/<sha256>.gz

and registered in raw_object (one row per unique content) and raw_fetch (one row per
retrieval: URL, time, HTTP status, headers). Identical content is stored once.
Objects are never modified or deleted by the pipeline, so any staging or warehouse
table can be rebuilt from here with `reitstreet replay`, without touching the network.

The Backend protocol keeps the door open for S3/Azure/GCS later (see ADR 0003).
"""

from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol


class IntegrityError(RuntimeError):
    pass


class Backend(Protocol):
    def exists(self, sha256: str) -> bool: ...
    def put(self, sha256: str, data: bytes) -> str: ...
    def get(self, sha256: str) -> bytes: ...


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalBackend:
    def __init__(self, root: Path):
        self.root = Path(root)

    def relpath(self, sha256: str) -> str:
        return f"sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}.gz"

    def path(self, sha256: str) -> Path:
        return self.root / self.relpath(sha256)

    def exists(self, sha256: str) -> bool:
        return self.path(sha256).exists()

    def put(self, sha256: str, data: bytes) -> str:
        dest = self.path(sha256)
        if dest.exists():
            return self.relpath(sha256)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
                gz.write(data)
            os.replace(tmp, dest)  # atomic: readers never see a partial object
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return self.relpath(sha256)

    def get(self, sha256: str) -> bytes:
        with gzip.open(self.path(sha256), "rb") as fh:
            return fh.read()


class RawStore:
    def __init__(self, backend: Backend):
        self.backend = backend

    @classmethod
    def local(cls, root: Path) -> RawStore:
        return cls(LocalBackend(root))

    def put(self, data: bytes) -> tuple[str, str]:
        sha = sha256_of(data)
        return sha, self.backend.put(sha, data)

    def get(self, sha256: str, verify: bool = True) -> bytes:
        data = self.backend.get(sha256)
        if verify and sha256_of(data) != sha256:
            raise IntegrityError(f"raw object {sha256} is corrupt (hash mismatch)")
        return data

    def exists(self, sha256: str) -> bool:
        return self.backend.exists(sha256)
