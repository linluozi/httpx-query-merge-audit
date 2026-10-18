"""Exercise the package the way a consumer would, in throwaway virtualenvs.

Three separate questions, each in a fresh venv so nothing leaks:

1. install      -- does ``pip install <wheel>`` give a working httpx?
2. coexist      -- after also installing real ``httpx==0.28.1``, which code wins?
3. uninstall    -- does removing the audit package break the real httpx?
4. extras       -- do ``[http2]`` / ``[socks]`` / ``[cli]`` resolve?
5. cli          -- is there an ``httpx`` console script?
6. namespace    -- is the distribution installed under the name ``httpx``?

Output: results/package.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

PROBE = r'''
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import importlib.metadata as md
out = {"import_ok": False, "file": None, "merge": None, "dists": []}
try:
    import httpx
    out["import_ok"] = True
    out["file"] = httpx.__file__
    out["merge"] = str(
        httpx.Request("GET", "https://e.com/s?a=1", params={"b": "2"}).url
    )
except Exception as exc:
    out["error"] = type(exc).__name__ + ": " + str(exc)
out["dists"] = sorted(
    {d.metadata["Name"] for d in md.distributions()
     if d.metadata["Name"] and "httpx" in d.metadata["Name"].lower()}
)
print(json.dumps(out))
'''


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
    )


def make_venv(root: Path) -> Path:
    venv.EnvBuilder(with_pip=True, clear=True).create(root)
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    return root / scripts / "python"


def probe(py: Path) -> dict:
    proc = run([str(py), "-c", PROBE])
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"import_ok": False, "error": "probe crashed: " + proc.stderr[-300:]}


def build_wheel(source: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    proc = run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(dest), str(source)]
    )
    wheels = sorted(dest.glob("*.whl"))
    if not wheels:
        raise SystemExit("wheel build failed:\n" + proc.stderr[-1500:])
    return wheels[-1]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--index", default="https://mirrors.aliyun.com/pypi/simple/")
    args = parser.parse_args()

    src = Path(args.source).resolve()
    findings: dict[str, object] = {}
    workspace = Path(tempfile.mkdtemp(prefix="httpx-pkg-"))
    try:
        wheel = build_wheel(src, workspace / "dist")
        findings["wheel"] = wheel.name
        print(f"built {wheel.name}")

        # 1 + 2 + 3: isolation, coexistence, removal
        py = make_venv(workspace / "v1")
        run([str(py), "-m", "pip", "install", "-q", f"--index-url={args.index}", str(wheel)])
        findings["install"] = probe(py)
        print("install:", findings["install"])

        run([str(py), "-m", "pip", "install", "-q", f"--index-url={args.index}",
             "httpx==0.28.1"])
        findings["coexist"] = probe(py)
        print("coexist:", findings["coexist"])

        run([str(py), "-m", "pip", "uninstall", "-y", "-q", "httpx-query-merge-patched"])
        findings["after_uninstall"] = probe(py)
        print("after uninstall:", findings["after_uninstall"])

        # 4: extras -- a missing extra only produces a warning, not a non-zero
        # exit, so the warning text is what actually has to be inspected.
        extras: dict[str, str] = {}
        for extra in ("http2", "socks", "cli", "brotli", "zstd"):
            py2 = make_venv(workspace / f"ex_{extra}")
            run([str(py2), "-m", "pip", "install", "-q", "--no-deps", str(wheel)])
            proc = run(
                [str(py2), "-m", "pip", "install", "-q", f"--index-url={args.index}",
                 f"{wheel}[{extra}]"]
            )
            blob = (proc.stdout or "") + (proc.stderr or "")
            if "does not provide the extra" in blob:
                extras[extra] = "no such extra (declared nowhere)"
            elif proc.returncode != 0:
                extras[extra] = "resolver error: " + blob.strip().splitlines()[-1][:90]
            else:
                extras[extra] = "resolved"
        findings["extras"] = extras
        print("extras:", extras)

        # 5 + 6: console script and distribution name
        py3 = make_venv(workspace / "v3")
        run([str(py3), "-m", "pip", "install", "-q", f"--index-url={args.index}", str(wheel)])
        scripts_dir = py3.parent
        cli = [p.name for p in scripts_dir.glob("httpx*") if p.suffix in ("", ".exe")]
        findings["console_scripts"] = cli
        print("console scripts:", cli or "none")

        # 7: does uninstalling the audit package delete httpx files?
        py4 = make_venv(workspace / "v4")
        run([str(py4), "-m", "pip", "install", "-q", f"--index-url={args.index}", str(wheel)])
        site_info = probe(py4)
        if site_info.get("file"):
            site = Path(site_info["file"]).parent
            before = len(list(site.rglob("*.py")))
            run([str(py4), "-m", "pip", "uninstall", "-y", "-q",
                 "httpx-query-merge-patched"])
            after = len(list(site.rglob("*.py"))) if site.exists() else 0
            findings["file_collateral"] = {
                "py_files_before": before,
                "py_files_after": after,
            }
            print("files before/after uninstall:", before, after)
        else:
            findings["file_collateral"] = {"error": site_info.get("error")}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
