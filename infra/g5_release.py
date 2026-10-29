"""Build a local source/model handoff bundle, excluding runtime state and secrets."""

import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from infra.manage import ROOT

EXCLUDE = {
    ".runtime",
    ".git",
    ".codex",
    ".agents",
    ".aws",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    ".impeccable",
}


def source_files():
    for current, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE)
        for name in sorted(files):
            path = Path(current) / name
            # The archive receipt is an external sidecar: including it would
            # recursively require the archive to contain its own final hash.
            if path == ROOT / 'docs/acceptance/g5-release.json':
                continue
            if name.startswith(".env") or path.suffix in {".pyc", ".log"} or path.is_symlink():
                continue
            yield path, path.relative_to(ROOT).as_posix()


def main():
    load_dotenv(ROOT / ".env")
    sensitive = [
        value.encode()
        for key, value in os.environ.items()
        if len(value) >= 12
        and any(s in key for s in ("PASSWORD", "TOKEN", "SECRET", "DATABASE_URL", "MIGRATION_URL"))
    ]
    items = list(source_files())
    model = ROOT / ".runtime/models/multilingual-minilm"
    items += [
        (p, "prepared-model/" + p.relative_to(model).as_posix())
        for p in model.rglob("*")
        if p.is_file()
    ]
    manifest = {
        "schema": "g5-handoff-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "files": {},
        "excluded": "env, runtime databases/artifacts/backups/logs, caches, installed dependencies",
        "model": "fixed public cached weights/config/tokenizer/card; no startup download",
        "installation": "locked dependency preparation still requires a populated package cache or network; do not claim offline install",
    }
    for path, name in items:
        data = path.read_bytes()
        if any(secret in data for secret in sensitive):
            raise ValueError("SECRET_SCAN_FAILED:" + name)
        manifest["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    target = ROOT / ".runtime/g5-handoff"
    target.mkdir(exist_ok=True)
    archive = target / ("hotel-project-g5-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as z:
        for path, name in items:
            z.write(path, name)
        z.writestr(
            "handoff-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for name, entry in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == entry["sha256"]
    report = {
        "schema": "g5-release-result-v1",
        "archive": str(archive),
        "bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": len(items),
        "secret_scan": "PASSED_EXACT_CURRENT_SECRET_VALUES",
        "archive_validation": "PASSED",
        "limitations": manifest["installation"],
    }
    (ROOT / "docs/acceptance/g5-release.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
