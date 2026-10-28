#!/usr/bin/env python3
"""Build a reproducible skill bundle from explicitly allowed source files."""
from pathlib import Path
import os
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

# Explicit paths prevent private .env/config files, SQLite databases, logs, and
# arbitrary user-created files under project directories from entering a bundle.
# New runtime modules or static assets must be deliberately added to this list.
FILES = (
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "README.md",
    "SKILL.md",
    "compose.yaml",
    "install.py",
    "main.py",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.lock",
    "requirements.txt",
    "daily_news/__init__.py",
    "daily_news/ai.py",
    "daily_news/archive.py",
    "daily_news/cli.py",
    "daily_news/config.example.toml",
    "daily_news/config.py",
    "daily_news/delivery.py",
    "daily_news/editorial.py",
    "daily_news/env.example",
    "daily_news/feeds.py",
    "daily_news/models.py",
    "daily_news/network.py",
    "daily_news/notifications.py",
    "daily_news/pipeline.py",
    "daily_news/presentation.py",
    "daily_news/render.py",
    "daily_news/scheduler.py",
    "daily_news/state.py",
    "daily_news/studio.css",
    "daily_news/studio.html",
    "daily_news/studio.js",
    "daily_news/studio.py",
    "daily_news/topics.py",
    "references/ai-providers.md",
    "references/archive.md",
    "references/configuration.md",
    "references/notifications.md",
    "references/operations.md",
    "references/topics.md",
    "scripts/package_skill.py",
    "tests/conftest.py",
    "tests/test_archive.py",
    "tests/test_content.py",
    "tests/test_delivery_network.py",
    "tests/test_integration.py",
    "tests/test_notifications.py",
    "tests/test_operations.py",
    "tests/test_parallel_fetch.py",
    "tests/test_presentation.py",
    "tests/test_schedule_options.py",
    "tests/test_studio.py",
    "tests/test_topic_balance.py",
    "tests/test_topics.py",
    "tests/test_translation.py",
)


def _allowed_paths() -> list[Path]:
    paths = []
    for name in FILES:
        path = ROOT / name
        # Resolve also rejects a symlink or junction leading to any other file,
        # even if its target is another file within the workspace.
        if not path.is_file() or path.resolve() != path:
            raise ValueError(f"打包文件不存在或包含符号链接：{name}")
        paths.append(path)
    return paths


def package(destination: Path) -> Path:
    destination = Path(destination)
    paths = _allowed_paths()
    if destination.resolve() in paths:
        raise ValueError("打包目标不能覆盖源码文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".skill-package-", dir=destination.parent)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(paths):
                relative = path.relative_to(ROOT).as_posix()
                entry = zipfile.ZipInfo("smart-daily-news/" + relative, date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, path.read_bytes())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


if __name__ == "__main__":
    print(package(ROOT / "dist" / "smart-daily-news.skill"))
