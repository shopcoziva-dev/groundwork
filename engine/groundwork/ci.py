#!/usr/bin/env python3
"""GitHub Actions entry: builds the gw.py command from the workflow inputs (env CMD, STORE, NICHE, ARGS) without a shell,
so the args box can never run anything but gw.py."""
import os
import shlex
import subprocess
import sys
from pathlib import Path

cmd, store, niche = os.environ.get("CMD", "status"), os.environ.get("STORE", "").strip(), os.environ.get("NICHE", "").strip()
extra = shlex.split(os.environ.get("ARGS", ""))
gw = [sys.executable, str(Path(__file__).with_name("gw.py"))]
if cmd == "new":
    if not niche:
        sys.exit("niche is required for new")
    argv = gw + ["new", niche] + extra
else:
    if not store:
        sys.exit("store is required")
    argv = gw + [cmd, "--store", store] + extra
print("$ gw.py " + " ".join(shlex.quote(a) for a in argv[2:]), flush=True)
sys.exit(subprocess.call(argv))
