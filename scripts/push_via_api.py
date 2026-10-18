"""Push the current git HEAD to GitHub via the Git Data API.

Why: on this machine ``github.com:443`` is frequently unreachable while
``api.github.com`` works, so ``git push`` cannot be used.  This script rebuilds
the tree object-by-object and asserts that the resulting root tree SHA equals
``git rev-parse HEAD^{tree}`` — which proves the remote content is byte-identical
to the local commit.

Empty repositories reject ``POST /git/blobs`` with 409, so a seed commit is
created first with ``PUT /contents/<file>``.

Usage:
    python scripts/push_via_api.py --repo owner/name --branch main
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=True
    ).stdout.strip()


def gh(*args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(
        ["gh", "api", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        input=stdin,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise SystemExit(f"gh api {' '.join(args)} failed:\n{proc.stderr[-1500:]}")
    return proc.stdout


def api(method: str, endpoint: str, payload: dict) -> dict:
    out = gh("-X", method, endpoint, "--input", "-", stdin=json.dumps(payload))
    return json.loads(out)


def remote_ref(repo: str, branch: str) -> str | None:
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/git/ref/heads/{branch}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout)["object"]["sha"]


def seed_if_empty(repo: str, branch: str) -> None:
    if remote_ref(repo, branch):
        return
    readme = (ROOT / "README.md").read_bytes()
    api(
        "PUT",
        f"repos/{repo}/contents/README.md",
        {
            "message": "seed",
            "content": base64.b64encode(readme).decode("ascii"),
            "branch": branch,
        },
    )
    print("seeded empty repository with README.md")


def create_blob(repo: str, data: bytes) -> str:
    return api(
        "POST",
        f"repos/{repo}/git/blobs",
        {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
    )["sha"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", default=None)
    args = parser.parse_args()

    seed_if_empty(args.repo, args.branch)
    parent = remote_ref(args.repo, args.branch)
    if parent is None:
        raise SystemExit("no remote ref after seeding")

    files = [p for p in sorted(ROOT.rglob("*")) if p.is_file()]
    files = [
        p
        for p in files
        if ".git" not in p.relative_to(ROOT).parts
        and "__pycache__" not in p.relative_to(ROOT).parts
    ]
    blobs = {p.relative_to(ROOT).as_posix(): create_blob(args.repo, p.read_bytes()) for p in files}
    print(f"uploaded {len(blobs)} blobs")

    # Group by directory, deepest first, so each tree can reference its children.
    dirs: dict[str, dict[str, str]] = {}
    for rel, sha in blobs.items():
        parent_dir, _, name = rel.rpartition("/")
        dirs.setdefault(parent_dir, {})[name] = sha

    # Build trees bottom-up.  Keys are directory paths with "" for the repo root;
    # depth is used for ordering because a two-level dir must exist before its
    # parent can reference it.
    children: dict[str, list[str]] = {}
    for d in dirs:
        if not d:
            continue  # the root has no parent, and must not parent itself
        dir_parent = d.rpartition("/")[0]
        children.setdefault(dir_parent, []).append(d)

    dir_shas: dict[str, str] = {}
    # Deepest first, and the repo root ("") always last, because it references
    # every top-level tree and therefore needs all of them built already.
    ordered = sorted(dirs, key=lambda x: (x == "", -x.count("/")))
    for d in ordered:
        entries = [
            {"path": name, "mode": "100644", "type": "blob", "sha": sha}
            for name, sha in dirs[d].items()
        ]
        for sub in children.get(d, []):
            entries.append(
                {
                    "path": sub.rpartition("/")[2],
                    "mode": "040000",
                    "type": "tree",
                    "sha": dir_shas[sub],
                }
            )
        tree = api("POST", f"repos/{args.repo}/git/trees", {"tree": entries})
        dir_shas[d] = tree["sha"]

    root_tree = dir_shas[""]
    local_tree = git("rev-parse", "HEAD^{tree}")
    print(f"remote root tree : {root_tree}")
    print(f"local  root tree : {local_tree}")
    if root_tree != local_tree:
        raise SystemExit("TREE MISMATCH — refusing to move the ref")

    message = args.message or git("log", "-1", "--pretty=%s")
    commit = api(
        "POST",
        f"repos/{args.repo}/git/commits",
        {"message": message, "tree": root_tree, "parents": [parent]},
    )
    api(
        "PATCH",
        f"repos/{args.repo}/git/refs/heads/{args.branch}",
        {"sha": commit["sha"], "force": True},
    )
    print(f"pushed commit {commit['sha'][:12]} to {args.repo}:{args.branch}")
    print("tree identical: yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
