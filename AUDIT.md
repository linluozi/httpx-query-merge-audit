# Detailed findings

Companion to `README.md`. Everything here is backed by a file in `results/`; the JSON
path is named next to each claim.

Audited artefact: `httpx-query-merge-patched.zip` — HTTPX 0.28.1 source tree with
`Request.__init__` changed to merge `params` into the URL query, plus a 9-test
regression file, a unified diff and four prose documents.

## 1. Provenance: the tree really is 0.28.1 plus one change

`results/tree_diff.json`, `results/upstream_hashes.json`, `results/patch_fidelity.json`

25 files are common to the shipped tree and the upstream tag. Exactly one differs:
`httpx/_models.py`. Ten files exist only in the ZIP and all are documentation, the diff,
the test file, `pyproject.toml`, and `py.typed` (which upstream also has, at 0 bytes, and
which the fetch script now retrieves so the comparison is complete).

The shipped diff, applied to pristine upstream with `patch --binary -p1`, produces files
that are byte-identical to the ones the ZIP ships — for both `_models.py` and the new test
file, with zero CRLF introductions. So the ZIP is internally consistent: what it says it
changed is what it changed.

The changed hunk:

```diff
-        self.url = URL(url) if params is None else URL(url, params=params)
+        if params is None:
+            self.url = URL(url)
+        else:
+            self.url = URL(url).copy_merge_params(params)
```

Semantically this is identical to the one-liner in upstream PR #3761. The expansion into
an `if` block changes nothing observable; the added comment is accurate.

## 2. The merge is correct for what it claims

`results/behavior_matrix.json` — 15 of 21 cases pass on the shipped arm, versus 4 of 21 on
0.28.1. The ten "semantic" cases all pass:

| Case | Baseline | Shipped |
|---|---|---|
| issue #3621 repro | drops `page=post&s=list` | preserved |
| same key override + unrelated key | drops unrelated | `a=new&keep=1&b=2` |
| `params={}` | drops entire query | query preserved |
| repeated keys + new key | drops all | `tag=python&tag=httpx&page=2` |
| `httpx.URL` input | drops | merged |
| no existing query | fine | fine |
| fragment | kept | kept |
| relative URL | works | works |
| duplicated key replaced | collapses | collapses identically |
| list values | dropped | expanded |

The PR #3759 implementation (merge inside `URL.__init__`) produces the same results for
all 21 cases, which is why upstream closed it as redundant rather than wrong.

## 3. Byte fidelity is not preserved, and cannot be with this approach

`results/behavior_matrix.json` — 6 of the 11 byte-fidelity cases fail on **every** merge
arm, including upstream PR #3761.

The mechanism: `copy_merge_params()` → `QueryParams.merge()` stores **decoded** values in
a dict, and `URL.copy_with(params=...)` re-encodes them with `urlencode`. Round-tripping
through decode/encode is lossy for anything that is not the canonical encoding:

```
sig=ab%2fcd   -> sig=ab%2Fcd     hex digits upper-cased
sig=ab%20cd   -> sig=ab+cd       %20 replaced by +
t=%7E         -> t=~             unreserved characters decoded
flag          -> flag=           valueless key gains "="
a=1&&b=2      -> a=1&b=2         empty segment dropped
```

Concrete impact: AWS SigV4, Azure SAS, Aliyun OSS and GCS V4 signed URLs all sign the
canonical query string. Change one byte and the server returns 403. This is a regression
relative to 0.28.1 *for callers who pass params to an already-signed URL* — but note the
baseline also breaks those callers, by dropping the signature entirely. So the patch turns
"silently sends an unsigned URL" into "sends a URL with an invalid signature". Neither is
good; the second is arguably more discoverable, but it is not a fix.

The only leak-free path is `params is None`, where the code does `URL(url)` and the raw
query bytes are never parsed:

```
https://e.com/p?sig=ab%2Fcd&q=a%20b   params=None -> unchanged, byte-for-byte
https://e.com/p?sig=ab%2Fcd&q=a%20b   params={}   -> sig=ab%2Fcd&q=a+b
```

`results/behavior_matrix.json` case `bytes_none_params_pct20` passes on all four arms;
`bytes_empty_params_pct20` fails on all three merge arms. The difference is exactly one
truthiness check.

## 4. The regression suite proves the bug, not the fix

`results/mutation.json`, `results/mutation_equivalence.json`

The suite is a genuine bug demonstration: 8 of 9 tests fail on 0.28.1
(`BASELINE_TESTS.txt` in the ZIP, reproduced here).

Against a *fixed* codebase, though, it is weak. Five wrong implementations were injected;
three survive all 9 tests:

| Injected fault | Suite result | Observable difference |
|---|---|---|
| `if not params:` instead of `is None` | 9 green | `params={}` re-encodes the query; `results/mutation_equivalence.json` shows `?q=a+b` instead of `?q=a%20b` |
| drop the `params is None` fast path | 9 green | presigned URL re-encoded even when the caller passed no params |
| merge then `copy_with(fragment=None)` | 9 green | `#sec2` disappears |

None of these is an equivalent mutant — each is shown to change at least one real output,
so the 9-test suite has three real blind spots. The missing assertions are cheap to write:
one byte-exact URL with `%20` in it, one fragment, one presigned URL.

The package's own `AUDIT_REPORT.md` claims "**9 passed**" as the evidence of correctness.
That number is accurate and insufficient: the tests are written from the same mental model
as the fix, so they check the merge happened, not that the result is faithful.

## 5. Distribution-name collision

`results/package.json`

```
pip install httpx_query_merge_patched-0.28.1+audit1-py3-none-any.whl
  -> import works, merge active, only dist name "httpx-query-merge-patched" present

pip install httpx==0.28.1
  -> both dists present; httpx/ now comes from the real wheel; merge silently gone

pip uninstall -y httpx-query-merge-patched
  -> 23 httpx/*.py deleted; import httpx raises ModuleNotFoundError;
     httpx-0.28.1.dist-info still installed, so pip believes httpx is fine
```

Root cause: `[tool.setuptools.packages.find] include = ["httpx*"]` with
`name = "httpx-query-merge-patched"`. `top_level.txt` is `httpx`, so both distributions own
the same files. pip tracks ownership per distribution; whichever was installed last wins at
import time, and uninstalling one removes files the other still claims.

Also missing relative to upstream: no `[project.optional-dependencies]`, so
`pip install <wheel>[http2]`, `[socks]`, `[cli]`, `[brotli]`, `[zstd]` all warn
("does not provide the extra"); and no `[project.scripts]`, so the `httpx` CLI is gone.

This is a packaging defect, not a code defect, and it is the more dangerous of the two:
it can corrupt an existing environment rather than just produce a 403.

## 6. Cost

`results/bench.json`

| Case | Baseline | Δ vs baseline (median of per-round ratios) | Noise floor (IQR) |
|---|---|---|---|
| URL with query + `params=` | 27.2 µs | 1.86x / 1.91x / 1.90x (3759 / 3761 / shipped) | 0.81–0.98x |
| no query + `params=` | 26.3 µs | 1.39x / 1.54x / 1.54x | 0.89–1.07x |
| URL with query, no `params=` | 18.5 µs | 0.99x / 0.97x / 0.99x | 1.02–1.18x |

The first two rows clear the noise floor comfortably. The third does not: every arm sits
inside the interval that two byte-identical copies of 0.28.1 produce, so the honest reading
is "no measurable change" — the fast path really is free. No ratio is claimed for it.

Quartiles rather than min/max are quoted because a single stalled round on a shared machine
can put a 2.3x outlier into the tail; the full spread is still in `results/bench.json` for
anyone who wants it.

Where the two extra microseconds go: `URL(url)` parses the whole URL, then `copy_with`
parses it again with the new query, so the merge path pays for a second full parse. A
single-pass construction would avoid that, but nothing in this patch attempts it.

Absolute times are Python 3.14.5 on Windows and should be read as order-of-magnitude only;
the ratios are the transferable part.

## 7. What a correct fix would need

Not required for the audit, but the gaps point somewhere obvious:

1. Keep the raw query bytes when no merge is required (`params is None` already does this).
2. For the merge, splice the *encoded* segments: append new `k=v` pairs to the raw query
   string instead of round-tripping through `QueryParams`. That preserves existing bytes
   and still overrides keys if you scan for the key prefix.
3. Ship as a fork or a plugin (`httpx` wrapper) rather than a distribution that writes into
   the `httpx/` namespace, or keep the name `httpx` and use a version-local suffix so pip
   treats it as the same project.
4. Add the three missing assertions — `%20`, fragment, presigned URL — to the suite.

## 8. Limits of this audit

- Only `tests/models/*.py` from upstream was run, against the shipped arm. The full upstream
  suite needs network fixtures and was out of scope; one upstream test
  (`test_request_params`) fails by construction under any merge fix, which is expected and
  was recorded rather than "fixed".
- One interpreter (3.14.5) and one OS (Windows) were executed. The code paths used are
  platform-independent, but no other combination was verified.
- No real HTTP was sent. Client-level cases use `MockTransport`.
- The `macOS` / `Linux` behaviour of `patch --binary` on the shipped diff was not checked;
  it was verified on Windows only, which is the stricter case.
