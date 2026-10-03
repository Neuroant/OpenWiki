"""The CLI reads stdin as UTF-8 whatever the console code page (v0.96.1).

The MCP server and the Claude Code hooks read JSON from stdin that their host sends as UTF-8. On
Windows a piped stdin defaults to the locale's code page, so before the fix every non-ASCII character
arrived garbled ("Lautstärke" -> "LautstÃ¤rke"). ``PYTHONIOENCODING=cp1252`` reproduces that default
on any platform.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = "Lautst\u00e4rke \u2014 5 \u20ac"


def _run(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH=str(ROOT))
    env.pop("PYTHONUTF8", None)
    return subprocess.run([sys.executable, "-c", code], input=(TEXT + "\n").encode("utf-8"),
                          capture_output=True, env=env, timeout=60)


def test_cp1252_pipe_garbles_without_the_fix():
    # the premise: a cp1252 stdin really does mangle UTF-8 input
    r = _run("import sys; print(sys.stdin.read().strip() == " + repr(TEXT) + ")")
    assert r.stdout.strip() == b"False"


def test_utf8_stdio_decodes_input_and_encodes_output_as_utf8():
    r = _run("import sys\n"
             "from openwiki.cli import _utf8_stdio\n"
             "_utf8_stdio()\n"
             "data = sys.stdin.read().strip()\n"
             "print(data == " + repr(TEXT) + ")\n"
             "print(data)\n")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    first, second = r.stdout.decode("utf-8").splitlines()[:2]
    assert first == "True"
    assert second == TEXT                     # and stdout writes it back as UTF-8
