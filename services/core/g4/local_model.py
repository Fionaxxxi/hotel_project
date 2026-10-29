"""Killable isolated model subprocess. Explicit preparation; never downloads on startup."""

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT / ".runtime/models/multilingual-minilm"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"


class LocalModel:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.responses = queue.Queue()

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            self.process = None

    def _read(self, process, responses):
        for line in process.stdout:
            responses.put(line)
        responses.put(None)

    def receive(self, timeout):
        try:
            line = self.responses.get(timeout=timeout)
            if line is None:
                raise RuntimeError("MODEL_PROCESS_EXIT")
            return json.loads(line)
        except queue.Empty:
            self.close()
            raise TimeoutError("MODEL_TIMEOUT") from None

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        manifest = json.loads((DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
        if manifest["revision"] != REVISION:
            raise ValueError("MODEL_REVISION")
        for name, info in manifest["files"].items():
            with (DIRECTORY / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != info["sha256"]:
                    raise ValueError("MODEL_CHECKSUM")
        # Pass only OS execution requirements; no DB/channel/password environment.
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}
        }
        env.update({"HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false", "PYTHONUTF8": "1"})
        self.responses = queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, "-s", "-m", "services.core.g4.model_process"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(
            target=self._read, args=(self.process, self.responses), daemon=True
        ).start()
        if not self.receive(120).get("ready"):
            self.close()
            raise RuntimeError("MODEL_NOT_READY")

    def rank(self, texts, timeout=15):
        with self.lock:
            if os.environ.get("G4_AI_ENABLED", "false").lower() != "true":
                raise RuntimeError("MODEL_DISABLED")
            start = time.monotonic()
            self.start()
            self.process.stdin.write(json.dumps({"texts": texts}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            result = self.receive(timeout)
            result["wall_seconds"] = time.monotonic() - start
            result["model_revision"] = REVISION
            return result
