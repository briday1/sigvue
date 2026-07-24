"""Atomic downloads and defensive archive extraction for scientific datasets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from math import isfinite
from pathlib import Path
import ssl
import tarfile
from tempfile import NamedTemporaryFile
import time
from urllib.request import Request, urlopen

import certifi


ProgressCallback = Callable[[int, int | None], None]
DEFAULT_TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())


@dataclass(frozen=True)
class RemoteFile:
    """One downloadable file and the integrity facts known about it."""

    url: str
    filename: str
    size: int | None = None
    checksum: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.url, str)
            or not isinstance(self.filename, str)
            or not self.url
            or not self.filename
            or self.filename in {".", ".."}
            or Path(self.filename).name != self.filename
            or any(character in self.filename for character in "\r\n\0")
        ):
            raise ValueError("Remote files require a URL and a plain filename")
        if self.size is not None and (
            isinstance(self.size, bool)
            or not isinstance(self.size, int)
            or self.size < 0
        ):
            raise ValueError("Remote file sizes must be non-negative integers")
        if self.checksum is not None:
            if not isinstance(self.checksum, str):
                raise TypeError("Remote file checksums must be strings")
            _checksum_parts(self.checksum)


def _checksum_parts(value: str) -> tuple[str, str]:
    algorithm, separator, digest = value.partition(":")
    if not separator:
        algorithm, digest = "sha256", algorithm
    algorithm = algorithm.lower()
    try:
        expected_length = (
            hashlib.new(algorithm, usedforsecurity=False).digest_size * 2
        )
    except ValueError as error:
        raise ValueError(f"Unsupported checksum algorithm: {algorithm}") from error
    if len(digest) != expected_length or any(
        character not in "0123456789abcdefABCDEF"
        for character in digest
    ):
        raise ValueError(f"Invalid {algorithm} checksum")
    return algorithm, digest.lower()


def file_checksum(
    path: str | Path,
    algorithm: str = "sha256",
    *,
    chunk_bytes: int = 1024 * 1024,
) -> str:
    """Stream a file checksum without loading the file into memory."""
    if (
        isinstance(chunk_bytes, bool)
        or not isinstance(chunk_bytes, int)
        or chunk_bytes < 1
    ):
        raise ValueError("chunk_bytes must be a positive integer")
    digest = hashlib.new(algorithm, usedforsecurity=False)
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_verified(path: Path, remote: RemoteFile, chunk_bytes: int) -> bool:
    if not path.is_file():
        return False
    if remote.size is not None and path.stat().st_size != remote.size:
        return False
    if remote.checksum is not None:
        algorithm, expected = _checksum_parts(remote.checksum)
        return file_checksum(path, algorithm, chunk_bytes=chunk_bytes) == expected
    return remote.size is not None


def download_file(
    remote: RemoteFile,
    directory: str | Path,
    *,
    user_agent: str = "Sigvue/1",
    chunk_bytes: int = 1024 * 1024,
    progress: ProgressCallback | None = None,
    tls_context: ssl.SSLContext | None = None,
    retries: int = 3,
    timeout: float = 30.0,
    preserve_existing: bool = False,
) -> Path:
    """Download one file atomically, reusing an existing verified copy.

    ``preserve_existing`` is intended for mutable companion metadata, such as
    SigMF annotations, that must not be replaced by the original remote file.
    """
    if (
        isinstance(chunk_bytes, bool)
        or not isinstance(chunk_bytes, int)
        or chunk_bytes < 1
    ):
        raise ValueError("chunk_bytes must be a positive integer")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or retries < 0
    ):
        raise ValueError("retries must be a non-negative integer")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be finite and positive")
    if progress is not None and not callable(progress):
        raise TypeError("progress must be callable or omitted")
    root = Path(directory).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / remote.filename
    if destination.is_symlink():
        raise RuntimeError(
            f"Refusing to use symlink download destination: {destination}"
        )
    if preserve_existing and destination.is_file():
        return destination
    if _is_verified(destination, remote, chunk_bytes):
        return destination

    request = Request(remote.url, headers={"User-Agent": user_agent})
    context = tls_context or DEFAULT_TLS_CONTEXT
    for attempt in range(retries + 1):
        received = 0
        expected_size = remote.size
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb",
                dir=root,
                prefix=f".{destination.name}.",
                suffix=".part",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                with urlopen(
                    request,
                    context=context,
                    timeout=timeout,
                ) as response:
                    if expected_size is None:
                        header = response.headers.get("Content-Length")
                        if header is not None:
                            expected_size = int(header)
                            if expected_size < 0:
                                raise RuntimeError(
                                    f"Invalid Content-Length for "
                                    f"{remote.filename}"
                                )
                    while chunk := response.read(chunk_bytes):
                        output.write(chunk)
                        received += len(chunk)
                        if progress is not None:
                            progress(received, expected_size)
            assert temporary is not None
            if expected_size is not None and received != expected_size:
                raise RuntimeError(
                    f"Size mismatch for {remote.filename}: "
                    f"expected {expected_size}, received {received}"
                )
            if remote.checksum is not None:
                algorithm, expected = _checksum_parts(remote.checksum)
                if (
                    file_checksum(
                        temporary,
                        algorithm,
                        chunk_bytes=chunk_bytes,
                    )
                    != expected
                ):
                    raise RuntimeError(
                        f"Checksum mismatch for {remote.filename}"
                    )
            temporary.replace(destination)
            return destination
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if attempt == retries:
                raise
            time.sleep(min(1.0 * (attempt + 1), 3.0))
    raise AssertionError("unreachable")


def safe_extract_tar(archive: str | Path, directory: str | Path) -> tuple[Path, ...]:
    """Extract a tar archive after rejecting traversal paths and links."""
    archive_path = Path(archive)
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    root = output.resolve()
    extracted: list[Path] = []
    with tarfile.open(archive_path) as bundle:
        members = bundle.getmembers()
        for member in members:
            target = (output / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"Unsafe archive member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(
                    f"Unsupported archive member type: {member.name}"
                )
            extracted.append(target)
        bundle.extractall(output, members=members)
    return tuple(extracted)
