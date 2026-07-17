"""Point Kaleido at the Chrome-for-Testing copy bundled by PyInstaller."""

from pathlib import Path
import os
import stat
import sys
import zipfile


bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
manifest = bundle_root / "kaleido_chrome_executable.txt"
is_chromium_wrapper = len(sys.argv) > 1 and sys.argv[1].endswith("_unix_pipe_chromium_wrapper.py")
if manifest.is_file() and not is_chromium_wrapper:
    chrome_root = bundle_root / "kaleido_chrome"
    archive = bundle_root / "kaleido_chrome.zip"
    if archive.is_file() and not chrome_root.exists():
        with zipfile.ZipFile(archive) as chrome_zip:
            chrome_zip.extractall(chrome_root)
            # Zip extraction does not consistently restore Unix executable
            # bits, which Chrome and its helper processes require.
            for member in chrome_zip.infolist():
                mode = member.external_attr >> 16
                extracted = chrome_root / member.filename
                if mode and extracted.exists():
                    try:
                        extracted.chmod(mode)
                    except OSError:
                        pass

    chrome = chrome_root / manifest.read_text(encoding="utf-8").strip()
    if chrome.is_file():
        try:
            chrome.chmod(chrome.stat().st_mode | stat.S_IXUSR)
        except OSError:
            pass
        os.environ["BROWSER_PATH"] = str(chrome)
