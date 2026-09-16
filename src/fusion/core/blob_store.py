"""Content-addressed local blob store for large config payloads (doc section 14:
"대용량 패키지·영상·대량 로그는 HTTPS로 전송한다"). Deliberately separate from
ConfigService/MQTT -- the bytes travel over plain HTTP(S) directly through this
store; only a small JSON descriptor referencing the stored blob (sha256+size)
goes through Stage/Apply, which is what keeps that descriptor small enough to
also fit an MQTT payload if ever needed (doc's own split between the two
channels: "작은 JSON 설정은 MQTT payload로 전달 가능하다. 대용량...은 HTTPS로
전송한다").

Doc section 15 step 1 ("임시 위치에 수신하고 크기·hash·경로·schema·참조·Plugin
호환성을 검증한다"): size and hash are what this generic byte store can check.
Path safety is automatic -- a blob is always named by its own content hash, never
by a caller-supplied filename. Schema/reference/Plugin-compatibility validation
is specific to what a given ``kind`` of config revision means and belongs to
whoever interprets the blob's contents after Stage, not to this store.

Doc: "broker가 파일 서버 역할을 한다고 가정하지 않는다. 파일 전송 endpoint/저장소는
배포 환경에서 별도로 구성한다" -- this is exactly that separately-configured
endpoint/storage; nothing here goes anywhere near MQTT.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024


class BlobTooLargeError(Exception):
    def __init__(self, size: int, max_bytes: int) -> None:
        super().__init__(f"blob size {size} exceeds max {max_bytes} bytes")
        self.size = size
        self.max_bytes = max_bytes


class BlobHashMismatchError(Exception):
    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"expected sha256 {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    size: int


class BlobStore:
    def __init__(self, root: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._root = root
        self._max_bytes = max_bytes

    def path_for(self, sha256: str) -> Path:
        return self._root / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    async def save_stream(
        self, chunks: AsyncIterator[bytes], *, expected_sha256: str | None = None
    ) -> BlobRef:
        self._root.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        size = 0
        tmp_path = self._root / f".tmp-{uuid.uuid4().hex}"
        try:
            with tmp_path.open("wb") as fh:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise BlobTooLargeError(size, self._max_bytes)
                    hasher.update(chunk)
                    fh.write(chunk)
            digest = hasher.hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise BlobHashMismatchError(expected_sha256, digest)
            tmp_path.replace(self.path_for(digest))
            return BlobRef(sha256=digest, size=size)
        finally:
            tmp_path.unlink(missing_ok=True)
