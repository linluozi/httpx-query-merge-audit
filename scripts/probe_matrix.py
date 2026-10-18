"""Run one behaviour matrix over every arm and record raw output per arm.

Each case declares the *intended* result.  Every arm is executed in its own
subprocess so no module cache can leak between arms (a real trap: swapping
``httpx`` mid-process keeps whatever the caller already imported).

Output: results/behavior_matrix.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# (id, description, expression-body, expected)
# The body is evaluated with ``httpx`` and ``U`` in scope and must return str.
CASES: list[tuple[str, str, str, str]] = [
    (
        "issue_repro",
        "Issue #3621 repro: URL query must survive a params= merge",
        'httpx.Request("GET", "https://safebooru.org/index.php?page=post&s=list",'
        ' params={"pid": 0, "tags": "k-on!"}).url',
        "https://safebooru.org/index.php?page=post&s=list&pid=0&tags=k-on%21",
    ),
    (
        "override_and_keep",
        "Same key is replaced, unrelated keys survive",
        'httpx.Request("GET", "https://example.com/search?a=old&keep=1",'
        ' params={"a": "new", "b": "2"}).url',
        "https://example.com/search?a=new&keep=1&b=2",
    ),
    (
        "empty_params_keeps_query",
        "params={} is a semantic no-op for the existing query",
        'httpx.Request("GET", "https://example.com/search?token=abc", params={}).url',
        "https://example.com/search?token=abc",
    ),
    (
        "repeated_keys_survive",
        "Repeated existing keys stay repeated when a new key is added",
        '";".join("%s=%s" % kv for kv in httpx.Request("GET",'
        ' "https://example.com/search?tag=python&tag=httpx", params={"page": 2})'
        ".url.params.multi_items())",
        "tag=python;tag=httpx;page=2",
    ),
    (
        "url_object_input",
        "An httpx.URL object as input is merged too",
        'httpx.Request("GET", httpx.URL("https://example.com/search?sort=recent"),'
        ' params={"page": 3}).url',
        "https://example.com/search?sort=recent&page=3",
    ),
    (
        "no_existing_query",
        "No pre-existing query: params behave as before",
        'httpx.Request("GET", "https://example.com/search", params={"q": "hello world"}).url',
        "https://example.com/search?q=hello+world",
    ),
    (
        "fragment_preserved",
        "Fragment survives the merge",
        'httpx.Request("GET", "https://example.com/s?a=1#sec2", params={"b": "2"}).url',
        "https://example.com/s?a=1&b=2#sec2",
    ),
    (
        "relative_url",
        "Relative URLs still work",
        'httpx.Request("GET", "/s?a=1", params={"b": "2"}).url',
        "/s?a=1&b=2",
    ),
    (
        "dup_key_override",
        "Supplying a key that appears twice collapses it",
        'httpx.Request("GET", "https://e.com/s?a=1&a=2", params={"a": "9"}).url',
        "https://e.com/s?a=9",
    ),
    (
        "list_value",
        "A list value expands to repeated keys",
        'httpx.Request("GET", "https://e.com/s?a=1", params={"t": ["x", "y"]}).url',
        "https://e.com/s?a=1&t=x&t=y",
    ),
    # --- byte-exactness cases: the interesting ones ---
    (
        "bytes_pct2F_upper",
        "Existing %2F stays %2F byte-for-byte",
        'httpx.Request("GET", "https://e.com/p?sig=ab%2Fcd", params={"x": "1"}).url',
        "https://e.com/p?sig=ab%2Fcd&x=1",
    ),
    (
        "bytes_pct2f_lower",
        "Existing lowercase %2f stays lowercase",
        'httpx.Request("GET", "https://e.com/p?sig=ab%2fcd", params={"x": "1"}).url',
        "https://e.com/p?sig=ab%2fcd&x=1",
    ),
    (
        "bytes_pct20",
        "Existing %20 stays %20 (not rewritten to +)",
        'httpx.Request("GET", "https://e.com/p?sig=ab%20cd", params={"x": "1"}).url',
        "https://e.com/p?sig=ab%20cd&x=1",
    ),
    (
        "bytes_tilde",
        "Existing literal ~ is not normalised away",
        'httpx.Request("GET", "https://e.com/p?t=%7E", params={"x": "1"}).url',
        "https://e.com/p?t=%7E&x=1",
    ),
    (
        "bytes_empty_key",
        "A valueless key keeps its exact form",
        'httpx.Request("GET", "https://e.com/p?flag", params={"x": "1"}).url',
        "https://e.com/p?flag&x=1",
    ),
    (
        "bytes_presigned_none",
        "params=None keeps a presigned URL byte-for-byte",
        'httpx.Request("GET", u_aws).url',
        "__U_AWS__",
    ),
    (
        "bytes_presigned_empty",
        "params={} keeps a presigned URL byte-for-byte",
        'httpx.Request("GET", u_aws, params={}).url',
        "__U_AWS__",
    ),
    (
        "client_bytes_empty",
        "Client.get(url, params={}) keeps the query byte-for-byte",
        '__client_url(url=u_aws, params={})',
        "__U_AWS__",
    ),
    (
        "client_bytes_merge",
        "Client.get(url) with client-level params keeps bytes and adds keys",
        '__client_url(url=u_aws, client_params={"api": "k"})',
        "__U_AWS__&api=k",
    ),
    # These two exist to separate "takes the fast path" from "merges":
    # both preserve the *set* of keys, only byte fidelity tells them apart.
    (
        "bytes_empty_params_pct20",
        "params={} must not re-encode an existing %20 as +",
        'httpx.Request("GET", "https://e.com/p?q=a%20b", params={}).url',
        "https://e.com/p?q=a%20b",
    ),
    (
        "bytes_none_params_pct20",
        "params=None must not re-encode an existing %20 as +",
        'httpx.Request("GET", "https://e.com/p?q=a%20b").url',
        "https://e.com/p?q=a%20b",
    ),
]

U_AWS = (
    "https://acct.blob.core.windows.net/c/b?sv=2022-11-02&ss=b&srt=sco&sp=rwdlacupiytfx"
    "&se=2027-01-01T00%3A00%3A00Z&st=2026-01-01T00%3A00%3A00Z&spr=https&sig=abc%2Fdef%3D"
)

RUNNER = r'''
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import httpx

u_aws = {u_aws!r}

def __client_url(url, params=None, client_params=None):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, request=request)
    kwargs = {{}}
    if client_params is not None:
        kwargs["params"] = client_params
    with httpx.Client(transport=httpx.MockTransport(handler), **kwargs) as client:
        client.get(url, params=params)
    return seen[0]

def evaluate(body):
    return str(eval(body, {{"httpx": httpx, "u_aws": u_aws, "__client_url": __client_url}}))

out = {{}}
for cid, body in {cases!r}:
    try:
        out[cid] = {{"ok": True, "value": evaluate(body)}}
    except Exception as exc:
        out[cid] = {{"ok": False, "error": type(exc).__name__ + ": " + str(exc)}}
print(json.dumps(out, ensure_ascii=False))
'''


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", required=True, help="directory holding the arm subdirectories")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    arms_root = Path(args.arms).resolve()
    arms = sorted(p.name for p in arms_root.iterdir() if (p / "httpx" / "__init__.py").is_file())

    cases_payload = [[cid, body] for cid, _desc, body, _exp in CASES]
    script = RUNNER.format(u_aws=U_AWS, cases=cases_payload)

    report: dict[str, object] = {"cases": [], "arms": {}}
    raw: dict[str, dict[str, dict]] = {}
    for arm in arms:
        env = dict(os.environ, PYTHONPATH=str(arms_root / arm))
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(arms_root),
        )
        if proc.returncode != 0:
            raise SystemExit(f"arm {arm} failed:\n{proc.stderr[-2000:]}")
        raw[arm] = json.loads(proc.stdout.strip().splitlines()[-1])

    for cid, desc, _body, expected in CASES:
        expected = U_AWS if expected == "__U_AWS__" else expected
        entry = {
            "id": cid,
            "description": desc,
            "expected": expected,
            "results": {},
        }
        for arm in arms:
            got = raw[arm][cid]
            value = got.get("value") if got["ok"] else got["error"]
            entry["results"][arm] = {
                "value": value,
                "ok": bool(got["ok"]) and value == expected,
            }
        report["cases"].append(entry)

    report["arms"] = arms
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    header = f"{'case':<26}" + "".join(f"{a:>12}" for a in arms)
    print(header)
    print("-" * len(header))
    for entry in report["cases"]:
        row = f"{entry['id']:<26}"
        for arm in arms:
            row += f"{'PASS' if entry['results'][arm]['ok'] else 'fail':>12}"
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
