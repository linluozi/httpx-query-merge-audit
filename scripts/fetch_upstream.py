"""Fetch the exact upstream files this audit compares against.

Uses the GitHub Contents API so the bytes are the real blob, not a checkout
that could be mangled by ``core.autocrlf``.  Every fetched file is hashed and
the hash is written to ``results/upstream_hashes.json`` so later checks can
prove the baseline was not silently swapped.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REF = "0.28.1"
REPO = "encode/httpx"
FILES = [
    "httpx/__init__.py",
    "httpx/__version__.py",
    "httpx/_api.py",
    "httpx/_auth.py",
    "httpx/_client.py",
    "httpx/_config.py",
    "httpx/_content.py",
    "httpx/_decoders.py",
    "httpx/_exceptions.py",
    "httpx/_main.py",
    "httpx/_models.py",
    "httpx/_multipart.py",
    "httpx/_status_codes.py",
    "httpx/_transports/__init__.py",
    "httpx/_transports/asgi.py",
    "httpx/_transports/base.py",
    "httpx/_transports/default.py",
    "httpx/_transports/mock.py",
    "httpx/_transports/wsgi.py",
    "httpx/_types.py",
    "httpx/_urlparse.py",
    "httpx/_urls.py",
    "httpx/_utils.py",
    "httpx/py.typed",
    "LICENSE.md",
]


def fetch(path: str) -> bytes:
    out = subprocess.run(
        ["gh", "api", f"repos/{REPO}/contents/{path}?ref={REF}", "--jq", ".content"],
        capture_output=True,
        check=True,
    )
    return base64.b64decode(out.stdout)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--hash-out", required=True)
    args = parser.parse_args()

    root = Path(args.out).resolve()
    hashes: dict[str, str] = {}
    for rel in FILES:
        data = fetch(rel)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hashes[rel] = hashlib.sha1(data).hexdigest()
        print(f"{hashes[rel]}  {len(data):>7}  {rel}")

    Path(args.hash_out).write_text(
        json.dumps({"repo": REPO, "ref": REF, "sha1": hashes}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
