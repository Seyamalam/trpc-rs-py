#!/usr/bin/env python3
"""Run every ```python code block in docs/ as a subprocess.

MISSION.md section 14 requires that code examples in the docs are tested.
This harness extracts each ```python fenced block from docs/**/*.md and
executes it:

* Plain blocks run as a script (``python snippet.py``) in a fresh subprocess
  with a 15s timeout and an isolated temporary working directory, using the
  same interpreter this script runs under — so ``trpc_rs_py`` and the dev
  dependencies installed by ``uv sync`` are importable.
* Blocks containing ``>>>`` prompts run through the doctest module instead
  (``python -m doctest snippet.txt``).

Blocks that cannot run standalone opt out with a marker comment on any line
inside the block::

    # snippet: skip — why it cannot run standalone

Everything after ``skip`` on the marker line is a human-readable reason shown
in the report. Legitimate reasons: excerpts that reference names defined only
in surrounding prose, snippets that start a server or need a live one, and
examples needing an optional dependency or a project module that does not
exist in this repo. An unmarked block that fails is a doc bug: the script
prints the block source and exits non-zero.

Run: uv run python scripts/test_doc_snippets.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
TIMEOUT_SECONDS = 15

_OPEN_FENCE = re.compile(r"^(`{3,})python[ \t]*$")
_CLOSE_FENCE = re.compile(r"^(`{3,})[ \t]*$")
_SKIP_MARKER = re.compile(r"^#\s*snippet:\s*skip\b[ \t]*[:\u2014\u2013-]?[ \t]*(.*?)[ \t]*$")
_DOCTEST_PROMPT = re.compile(r"^>>>")


@dataclass(frozen=True)
class Snippet:
    """One ```python block extracted from a docs page.

    Attributes:
        path: Markdown file the block came from, relative to the repo root.
        line: 1-based line number of the first content line inside the fence.
        source: The block's contents, fence markers excluded.
        skip_reason: Text after ``# snippet: skip`` when the block is marked
            unrunnable, else None.
        is_doctest: True when the block contains ``>>>`` prompts and must be
            checked with the doctest module instead of executed as a script.
    """

    path: Path
    line: int
    source: str
    skip_reason: str | None
    is_doctest: bool


def iter_snippets(path: Path, rel: Path) -> Iterator[Snippet]:
    """Yield every ```python fenced block in one Markdown file.

    Only fences made of three or more backticks are considered; the closing
    fence must be at least as long as the opening one, per CommonMark.
    """
    lines = path.read_text().split("\n")
    index = 0
    while index < len(lines):
        match = _OPEN_FENCE.match(lines[index].strip())
        if match is None:
            index += 1
            continue
        tick_count = len(match.group(1))
        body_start = index + 1
        end = body_start
        while end < len(lines):
            closing = _CLOSE_FENCE.match(lines[end].strip())
            if closing is not None and len(closing.group(1)) >= tick_count:
                break
            end += 1
        body = "\n".join(lines[body_start:end])
        skip_reason: str | None = None
        for block_line in lines[body_start:end]:
            marker = _SKIP_MARKER.match(block_line.strip())
            if marker is not None:
                skip_reason = marker.group(1) or "marked unrunnable"
                break
        yield Snippet(
            path=rel,
            line=body_start + 1,
            source=body,
            skip_reason=skip_reason,
            is_doctest=any(_DOCTEST_PROMPT.match(line) for line in lines[body_start:end]),
        )
        index = end + 1


def collect_snippets() -> list[Snippet]:
    """Extract all ```python blocks from every Markdown file under docs/."""
    snippets: list[Snippet] = []
    for md in sorted(DOCS_DIR.rglob("*.md")):
        snippets.extend(iter_snippets(md, md.relative_to(ROOT)))
    return snippets


def run_snippet(snippet: Snippet) -> str | None:
    """Execute one snippet; return None on success or an error description."""
    suffix = ".txt" if snippet.is_doctest else ".py"
    with tempfile.TemporaryDirectory() as workdir:
        target = Path(workdir) / f"snippet{suffix}"
        target.write_text(snippet.source)
        command = [sys.executable, str(target)]
        if snippet.is_doctest:
            command = [sys.executable, "-m", "doctest", str(target)]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            return f"timed out after {TIMEOUT_SECONDS}s"
    if result.returncode == 0:
        return None
    detail = result.stderr.strip() or result.stdout.strip() or "no output"
    return f"exit code {result.returncode}\n{detail}"


def main() -> int:
    """Run every snippet and print the report; return 1 on any failure."""
    files = list(DOCS_DIR.rglob("*.md"))
    snippets = collect_snippets()
    passed: list[Snippet] = []
    skipped: list[Snippet] = []
    failed: list[tuple[Snippet, str]] = []
    for snippet in snippets:
        if snippet.skip_reason is not None:
            skipped.append(snippet)
            continue
        error = run_snippet(snippet)
        if error is None:
            passed.append(snippet)
        else:
            failed.append((snippet, error))

    for snippet, error in failed:
        print(f"FAIL {snippet.path}:{snippet.line}")
        print("----- block -----")
        print(snippet.source)
        print("----- error -----")
        print(error)
        print("-----------------")
    for snippet in skipped:
        print(f"skip {snippet.path}:{snippet.line} — {snippet.skip_reason}")

    print(
        f"doc snippets: {len(files)} files scanned, "
        f"{len(passed)} run and passed, {len(skipped)} skipped, "
        f"{len(failed)} failed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
