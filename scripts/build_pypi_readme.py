#!/usr/bin/env python3
"""Render README Mermaid blocks as SVG images for the PyPI description."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path


MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)
HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "diagram"


def build_readme(
    source: Path,
    output: Path,
    image_dir: Path,
    *,
    repository: str,
    ref: str,
    renderer: str,
    puppeteer_config: Path | None = None,
) -> int:
    markdown = source.read_text(encoding="utf-8")
    matches = list(MERMAID_BLOCK.finditer(markdown))
    if not matches:
        raise SystemExit(f"No Mermaid blocks found in {source}")

    image_dir.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    replacements: list[tuple[int, int, str]] = []
    names: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix="sigvue-mermaid-") as temporary:
        temporary_dir = Path(temporary)
        for index, match in enumerate(matches, start=1):
            headings = list(HEADING.finditer(markdown, 0, match.start()))
            title = headings[-1].group("title") if headings else f"Diagram {index}"
            base = _slug(title)
            names[base] = names.get(base, 0) + 1
            suffix = f"-{names[base]}" if names[base] > 1 else ""
            filename = f"{index:02d}-{base}{suffix}.svg"
            destination = image_dir / filename
            expected.add(destination)

            definition = temporary_dir / f"{index:02d}.mmd"
            definition.write_text(match.group("body").rstrip() + "\n", encoding="utf-8")
            command = [renderer]
            if puppeteer_config is not None:
                command.extend(["--puppeteerConfigFile", str(puppeteer_config)])
            command.extend(
                [
                    "--input",
                    str(definition),
                    "--output",
                    str(destination),
                    "--backgroundColor",
                    "transparent",
                ]
            )
            subprocess.run(command, check=True)

            image_url = (
                f"https://raw.githubusercontent.com/{repository}/{ref}/"
                f"{image_dir.as_posix()}/{filename}"
            )
            replacements.append(
                (match.start(), match.end(), f"![{title} diagram]({image_url})")
            )

    for stale in image_dir.glob("*.svg"):
        if stale not in expected:
            stale.unlink()

    generated = markdown
    for start, end, replacement in reversed(replacements):
        generated = generated[:start] + replacement + generated[end:]
    notice = (
        "<!-- Generated from README.md by scripts/build_pypi_readme.py. "
        "Do not edit directly. -->\n\n"
    )
    output.write_text(notice + generated, encoding="utf-8")
    return len(matches)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("README.md"))
    parser.add_argument("--output", type=Path, default=Path("README_PYPI.md"))
    parser.add_argument(
        "--image-dir", type=Path, default=Path("docs/pypi-diagrams")
    )
    parser.add_argument("--repository", default="briday1/sigvue")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--renderer", default="mmdc")
    parser.add_argument("--puppeteer-config", type=Path)
    arguments = parser.parse_args()

    count = build_readme(
        arguments.source,
        arguments.output,
        arguments.image_dir,
        repository=arguments.repository,
        ref=arguments.ref,
        renderer=arguments.renderer,
        puppeteer_config=arguments.puppeteer_config,
    )
    print(f"Rendered {count} Mermaid diagrams into {arguments.output}")


if __name__ == "__main__":
    main()
