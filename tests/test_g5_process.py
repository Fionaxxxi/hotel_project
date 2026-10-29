import os
import subprocess
import sys

import pytest

from infra.g5_process import verified
from infra.manage import ROOT


def test_wrong_working_directory_pid_is_not_stopped():
    p = subprocess.Popen(
        [sys.executable, "-s", "-c", "import time;time.sleep(30)"],
        cwd=ROOT / ".runtime",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with pytest.raises(ValueError, match="PID_DOES_NOT_MATCH"):
            verified(p.pid, "-c")
        assert p.poll() is None
    finally:
        p.terminate()
        p.wait(timeout=10)
