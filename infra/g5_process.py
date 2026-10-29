"""Stop only a PID whose interpreter, exact module argument and cwd all match."""

import argparse
import os
import sys
from pathlib import Path

import psutil

from infra.manage import ROOT


def verified(pid, module):
    p = psutil.Process(pid)
    args = p.cmdline()
    if (
        os.path.normcase(p.exe()) != os.path.normcase(sys.executable)
        or module not in args
        or "-s" not in args
        or Path(p.cwd()).resolve() != ROOT.resolve()
    ):
        raise ValueError("PID_DOES_NOT_MATCH_PROJECT_INTERPRETER_MODULE_AND_CWD")
    return p


def main(pid, module):
    try:
        p = verified(pid, module)
    except psutil.NoSuchProcess:
        return
    p.terminate()
    p.wait(timeout=10)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--module", required=True)
    args = p.parse_args()
    main(args.pid, args.module)
