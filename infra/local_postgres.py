"""Operate only the project-owned local PostgreSQL cluster; no Windows service changes."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
RUNTIME = ROOT / ".runtime"
DATA = RUNTIME / "postgres-g0"
BIN = Path(sys.prefix) / "Library/bin"


def run(*args):
    env = os.environ.copy()
    env["PATH"] = str(BIN) + os.pathsep + sys.prefix + os.pathsep + env["PATH"]
    result = subprocess.run(
        [str(BIN / args[0]), *map(str, args[1:])],
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.returncode


def start():
    RUNTIME.mkdir(exist_ok=True)
    if DATA.exists() and not (DATA / "global/pg_control").exists():
        raise RuntimeError("Incomplete project cluster; inspect .runtime before recovery")
    if not DATA.exists():
        password_file = RUNTIME / "bootstrap-password.tmp"
        password_file.write_text(os.environ["HOTEL_POSTGRES_PASSWORD"], encoding="ascii")
        try:
            code = run(
                "initdb.exe",
                "-D",
                DATA,
                "-U",
                "postgres",
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C",
                "--pwfile",
                password_file,
            )
            if code:
                raise SystemExit(code)
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA / "postgresql.conf").open("a", encoding="utf-8") as stream:
            stream.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")
    if run("pg_ctl.exe", "-D", DATA, "status") == 0:
        return
    raise SystemExit(run("pg_ctl.exe", "-D", DATA, "-l", RUNTIME / "postgres.log", "-w", "start"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["start", "stop", "status"])
    command = parser.parse_args().command
    if command == "start":
        start()
    else:
        raise SystemExit(
            run("pg_ctl.exe", "-D", DATA, command, *(["-m", "fast"] if command == "stop" else []))
        )
