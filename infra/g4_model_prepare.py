"""Explicit public weights download. Never sends hotel data or loads remote code."""

import hashlib
import json

import httpx

from infra.manage import ROOT

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
FILES = ["onnx/model_quint8_avx2.onnx", "tokenizer.json", "config.json", "README.md"]
DIRECTORY = ROOT / ".runtime/models/multilingual-minilm"


def main():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    checksums = {}
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        for filename in FILES:
            path = DIRECTORY / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                temporary = path.with_suffix(path.suffix + ".part")
                with client.stream(
                    "GET", f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{filename}"
                ) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            output.write(chunk)
                temporary.replace(path)
            checksums[filename] = {
                "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
                "bytes": path.stat().st_size,
            }
            print("Prepared public model file:", filename, path.stat().st_size)
    (DIRECTORY / "manifest.json").write_text(
        json.dumps(
            {"model": MODEL, "revision": REVISION, "license": "Apache-2.0", "files": checksums},
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
