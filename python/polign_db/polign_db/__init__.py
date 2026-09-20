"""The polign_db binaries, installed by pip.

The wheel puts ``polign`` (the CLI) and ``polign-server`` into the
environment's scripts directory, next to ``python`` itself, so an activated
environment has both on ``PATH``. ``find_bin`` is for code that runs without
an activated environment, such as ``/srv/app/.venv/bin/python worker.py``.
"""

from __future__ import annotations

import os
import sys
import sysconfig

__version__ = "0.0.0"  # replaced with the release version by build_wheels.py

BINARIES = ("polign", "polign-server")


def find_bin(name: str = "polign") -> str:
    """Absolute path of an installed binary, ``polign`` or ``polign-server``."""
    if name not in BINARIES:
        raise ValueError(f"unknown binary {name!r}; this package installs {', '.join(BINARIES)}")
    exe = name + (".exe" if sys.platform == "win32" else "")
    candidates = [os.path.join(sysconfig.get_path("scripts"), exe)]
    # `pip install --user` uses a different scheme from the interpreter's own.
    user_scheme = sysconfig.get_preferred_scheme("user") if sys.version_info >= (3, 10) else None
    if user_scheme:
        candidates.append(os.path.join(sysconfig.get_path("scripts", scheme=user_scheme), exe))
    # `pip install --target` puts scripts in <target>/bin next to the package.
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", exe))
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"{exe} is not installed where pip puts scripts; looked in {', '.join(candidates)}")
