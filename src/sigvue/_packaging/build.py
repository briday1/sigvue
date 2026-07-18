"""Build the standalone executable from package-installed support files."""

from __future__ import annotations

from importlib.resources import as_file, files
import sys


def main() -> None:
    try:
        from PyInstaller.__main__ import run
    except ImportError as exc:  # pragma: no cover - depends on optional build extra
        raise SystemExit('Install build support first: pip install "sigvue[build]"') from exc

    arguments = sys.argv[1:] or ["--clean", "--noconfirm"]
    resource = files("sigvue._packaging").joinpath("sigvue.spec")
    with as_file(resource) as spec_path:
        run([*arguments, str(spec_path)])


if __name__ == "__main__":
    main()
