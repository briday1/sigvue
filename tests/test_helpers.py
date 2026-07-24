import io
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from sigvue.helpers import (
    RemoteFile,
    WorkspaceConfig,
    download_file,
    file_checksum,
    format_bytes,
    resident_nbytes,
    safe_extract_tar,
)


class FrameworkNeutralHelperTests(unittest.TestCase):
    def test_workspace_config_resolves_paths_and_typed_values(self):
        config = WorkspaceConfig({
            "profile_dir": "/tmp/profile",
            "data_root": "recordings",
            "pattern": "*.sigmf-meta",
            "gain": 2,
            "channels": 4,
            "enabled": True,
        })
        self.assertEqual(
            Path("/tmp/profile/recordings"),
            config.path("data_root", "data"),
        )
        self.assertEqual("*.sigmf-meta", config.string("pattern", "*"))
        self.assertEqual(2.0, config.floating("gain", 1.0))
        self.assertEqual(4, config.integer("channels", 1))
        self.assertTrue(config.boolean("enabled", False))
        with self.assertRaisesRegex(TypeError, "numeric"):
            WorkspaceConfig({"gain": "high"}).floating("gain", 1.0)

    def test_formatting_and_resident_memory_are_reusable(self):
        samples = np.zeros(16, dtype=np.complex64)
        self.assertEqual("1.00 KiB", format_bytes(1024))
        self.assertEqual(samples.nbytes, resident_nbytes(samples, samples))
        self.assertEqual(
            2 * samples.nbytes,
            resident_nbytes(samples, samples, deduplicate=False),
        )

    def test_verified_download_preservation_and_repair(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"verified payload")
            checksum = file_checksum(source)
            progress = []
            remote = RemoteFile(
                source.as_uri(),
                "copy.bin",
                size=source.stat().st_size,
                checksum=f"sha256:{checksum}",
            )
            destination = download_file(
                remote,
                root / "downloads",
                progress=lambda received, total: progress.append(
                    (received, total)
                ),
            )
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertTrue(progress)

            destination.write_bytes(b"locally annotated")
            preserved = download_file(
                RemoteFile(source.as_uri(), "copy.bin"),
                root / "downloads",
                preserve_existing=True,
            )
            self.assertEqual(b"locally annotated", preserved.read_bytes())

            destination.write_bytes(b"wrong")
            repaired = download_file(remote, root / "downloads")
            self.assertEqual(source.read_bytes(), repaired.read_bytes())

    def test_download_paths_and_temporary_files_cannot_escape(self):
        with self.assertRaisesRegex(ValueError, "plain filename"):
            RemoteFile("https://example.test/value", "..")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.bin"
            source.write_bytes(b"new")
            downloads = root / "downloads"
            downloads.mkdir()
            victim = root / "victim.bin"
            victim.write_bytes(b"keep")
            stale = downloads / ".copy.bin.part"
            stale.symlink_to(victim)

            copied = download_file(
                RemoteFile(
                    source.as_uri(),
                    "copy.bin",
                    size=3,
                    checksum=file_checksum(source),
                ),
                downloads,
            )
            self.assertEqual(b"new", copied.read_bytes())
            self.assertEqual(b"keep", victim.read_bytes())

            linked_destination = downloads / "linked.bin"
            linked_destination.symlink_to(victim)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                download_file(
                    RemoteFile(source.as_uri(), "linked.bin", size=3),
                    downloads,
                )

    def test_download_rejects_truncated_content_length(self):
        class TruncatedResponse:
            headers = {"Content-Length": "12"}

            def __init__(self):
                self.sent = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, count):
                del count
                if self.sent:
                    return b""
                self.sent = True
                return b"short"

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "sigvue.helpers.downloads.urlopen",
                    return_value=TruncatedResponse(),
                ),
                self.assertRaisesRegex(RuntimeError, "Size mismatch"),
            ):
                download_file(
                    RemoteFile(
                        "https://example.test/truncated",
                        "truncated.bin",
                    ),
                    root,
                    retries=0,
                )
            self.assertEqual((), tuple(root.iterdir()))

    def test_safe_tar_rejects_traversal_and_special_members(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "safe.tar"
            with tarfile.open(archive, "w") as bundle:
                info = tarfile.TarInfo("nested/value.txt")
                payload = b"safe"
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
            safe_extract_tar(archive, root / "unpacked")
            self.assertEqual(
                "safe",
                (root / "unpacked/nested/value.txt").read_text(),
            )

            unsafe = root / "unsafe.tar"
            with tarfile.open(unsafe, "w") as bundle:
                info = tarfile.TarInfo("../escape.txt")
                info.size = 1
                bundle.addfile(info, io.BytesIO(b"x"))
            with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                safe_extract_tar(unsafe, root / "rejected")

            linked = root / "linked.tar"
            with tarfile.open(linked, "w") as bundle:
                info = tarfile.TarInfo("link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../escape.txt"
                bundle.addfile(info)
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                safe_extract_tar(linked, root / "rejected-link")


if __name__ == "__main__":
    unittest.main()
