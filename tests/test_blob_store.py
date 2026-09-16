"""BlobStore (doc section 14/15): content-addressed local storage for large config
payloads. Size and hash are the two things this generic store validates -- path
safety is automatic since files are always named by their own content hash.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from fusion.core.blob_store import BlobHashMismatchError, BlobStore, BlobTooLargeError


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_save_stream_stores_content_at_its_own_sha256(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    data = b"hello fusion"

    ref = await store.save_stream(_chunks(data))

    assert ref.sha256 == hashlib.sha256(data).hexdigest()
    assert ref.size == len(data)
    assert store.exists(ref.sha256)
    assert store.path_for(ref.sha256).read_bytes() == data


async def test_save_stream_assembles_multiple_chunks(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    ref = await store.save_stream(_chunks(b"abc", b"def", b"ghi"))

    assert ref.size == 9
    assert store.path_for(ref.sha256).read_bytes() == b"abcdefghi"


async def test_save_stream_rejects_hash_mismatch(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)

    with pytest.raises(BlobHashMismatchError):
        await store.save_stream(_chunks(b"hello"), expected_sha256="0" * 64)

    # nothing left behind on rejection
    assert list(tmp_path.iterdir()) == []


async def test_save_stream_accepts_matching_expected_hash(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    data = b"hello fusion"
    digest = hashlib.sha256(data).hexdigest()

    ref = await store.save_stream(_chunks(data), expected_sha256=digest)

    assert ref.sha256 == digest


async def test_save_stream_rejects_oversized_content(tmp_path: Path) -> None:
    store = BlobStore(tmp_path, max_bytes=4)

    with pytest.raises(BlobTooLargeError):
        await store.save_stream(_chunks(b"toolong"))

    assert list(tmp_path.iterdir()) == []


def test_exists_is_false_for_unknown_hash(tmp_path: Path) -> None:
    store = BlobStore(tmp_path)
    assert store.exists("0" * 64) is False
