"""Materialise four copies of httpx 0.28.1 to compare behaviour.

Arms
----
upstream   : pristine httpx 0.28.1 runtime (the buggy baseline)
pr3761     : the one-line upstream pull request #3761 applied
pr3759     : the rejected pull request #3759 applied (URL.__init__ merges)
shipped    : the source package inside httpx-query-merge-patched.zip

Run with ``--source <dir>`` pointing at an extracted copy of the ZIP and
``--upstream <dir>`` pointing at an extracted copy of httpx 0.28.1.  Nothing
here downloads anything; see ``fetch_upstream.py`` for that.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ARMS = ("upstream", "pr3761", "pr3759", "shipped")

PR3761_OLD = (
    "        self.url = URL(url) if params is None else URL(url, params=params)\n"
)
PR3761_NEW = (
    "        self.url = URL(url) if params is None else "
    "URL(url).copy_merge_params(params)\n"
)

# PR #3759 merges inside URL.__init__ instead of inside Request.__init__.
# Copied from the real diff of encode/httpx#3759 (closed, superseded by #3761).
PR3759_OLD = """            if "params" in kwargs:
                # Replace any "params" keyword with the raw "query" instead.
                #
                # Ensure that empty params use `kwargs["query"] = None` rather
                # than `kwargs["query"] = ""`, so that generated URLs do not
                # include an empty trailing "?".
                params = kwargs.pop("params")
                kwargs["query"] = None if not params else str(QueryParams(params))
"""
PR3759_NEW = """            if "params" in kwargs:
                # Merge any "params" keyword with existing query params.
                #
                # When a URL already has query parameters and additional params
                # are provided, we merge them together rather than replacing.
                # This matches the behavior of the requests library.
                #
                # Ensure that empty params use `kwargs["query"] = None` rather
                # than `kwargs["query"] = ""`, so that generated URLs do not
                # include an empty trailing "?".
                params = kwargs.pop("params")

                # If params is a QueryParams object, use it directly
                # (for copy_* methods like copy_remove_param)
                if isinstance(params, QueryParams):
                    kwargs["query"] = str(params) if params else None
                else:
                    # Get existing query params from the URL
                    if isinstance(url, str):
                        parsed_url = urlparse(url)
                        existing_params = (
                            QueryParams(parsed_url.query)
                            if parsed_url.query
                            else QueryParams()
                        )
                    else:  # isinstance(url, URL)
                        existing_params = url.params

                    # Merge existing and new params
                    if params:
                        merged_params = existing_params.merge(params)
                        kwargs["query"] = str(merged_params) if merged_params else None
                    elif existing_params:
                        # params is empty but URL has existing params - keep them
                        kwargs["query"] = str(existing_params)
                    else:
                        # Both empty - no query string
                        kwargs["query"] = None
"""


def copy_arm(upstream: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        upstream, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )


def patch_file(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8", newline="")
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
    return True


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True, help="pristine httpx 0.28.1 package root")
    parser.add_argument("--source", required=True, help="extracted ZIP package root")
    parser.add_argument("--out", required=True, help="directory to place the four arms")
    args = parser.parse_args()

    upstream = Path(args.upstream).resolve()
    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    copy_arm(upstream, out / "upstream")

    copy_arm(upstream, out / "pr3761")
    if not patch_file(out / "pr3761" / "httpx" / "_models.py", PR3761_OLD, PR3761_NEW):
        print("FATAL: PR #3761 anchor not found")
        return 1

    copy_arm(upstream, out / "pr3759")
    if not patch_file(out / "pr3759" / "httpx" / "_urls.py", PR3759_OLD, PR3759_NEW):
        print("FATAL: PR #3759 anchor not found")
        return 1

    copy_arm(source, out / "shipped")
    shutil.rmtree(out / "shipped" / "tests", ignore_errors=True)

    for arm in ARMS:
        init = out / arm / "httpx" / "__init__.py"
        print(f"{arm:<10} {'ok' if init.is_file() else 'MISSING'}  {init}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
