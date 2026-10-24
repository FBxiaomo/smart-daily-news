#!/usr/bin/env python3
"""Create a reproducible source-only skill archive; never include local config, data, or credentials."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "SKILL.md", "README.md", "main.py", "install.py", "pyproject.toml",
    "requirements.txt", "requirements.lock", "requirements-dev.txt",
    "Dockerfile", "compose.yaml", ".dockerignore", ".gitignore",
]
DIRECTORIES = ["daily_news", "references", "scripts", "tests"]


def package(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts
                     and path.suffix in (".py", ".md", ".toml", ".example"))
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            relative = path.relative_to(ROOT).as_posix()
            entry = zipfile.ZipInfo("smart-daily-news/" + relative, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
    return destination


if __name__ == "__main__":
    print(package(ROOT / "dist" / "smart-daily-news.skill"))
