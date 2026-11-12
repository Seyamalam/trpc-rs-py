#!/usr/bin/env python3
"""Sync ``docs/`` (MkDocs source of truth) into the Fumadocs app in ``apps/docs``.

The repo keeps one copy of the documentation — ``docs/`` — which ``just ci``
gates on (mkdocs --strict, snippet tests, matrix checks). This script renders
that content into ``apps/docs/content/docs`` as MDX so the published site
never drifts:

* nav order in ``mkdocs.yml`` becomes ``meta.json`` files (section titles and
  page order) — a page's *output* location follows its nav position, not its
  file location;
* ``# Title`` becomes frontmatter ``title``; the first paragraph becomes
  ``description``;
* `` ```mermaid `` fences become ``<Mermaid chart={...}/>`` components;
* mkdocstrings ``::: module`` directives (Python-docstring rendering is
  MkDocs-only) become links to the module source on GitHub;
* relative ``.md`` links are resolved to ``/docs/...`` site URLs using the
  nav map, ``#anchor`` fragments preserved;
* image/binary assets are copied to ``apps/docs/public`` and re-rooted;
* leftover ``{``/``}``/``<`` outside code spans and fences are escaped so MDX
  does not parse them as JSX.

Usage: ``uv run python scripts/sync_docs_site.py`` (idempotent — the whole
generated tree under ``apps/docs/content`` and ``apps/docs/public`` is
rewritten on every run).
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SITE = ROOT / "apps" / "docs"
CONTENT = SITE / "content" / "docs"
PUBLIC = SITE / "public"
MKDOCS = ROOT / "mkdocs.yml"
GITHUB_BLOB = "https://github.com/Seyamalam/trpc-rs-py/blob/main/docs"
GITHUB_SRC = "https://github.com/Seyamalam/trpc-rs-py/blob/main/python"

FENCE_RE = re.compile(r"^\s*```(\w*)\s*$")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
INDEX_STEMS = {"index", "readme"}


def nav_entries(node: object) -> list[tuple[str, object]]:
    """Flatten a mkdocs nav node into (title, value) pairs in order."""
    out: list[tuple[str, object]] = []
    for item in node if isinstance(node, list) else [node]:
        if isinstance(item, dict):
            out.extend(item.items())
        elif isinstance(item, str):
            out.append((item, item))
    return out


def folder_for(title: str, children: list[tuple[str, object]]) -> str:
    """Return the output dir for a nav section.

    The shared source dir if its children already live in one (``security/*``),
    else a slug of the section title.
    """
    dirs = {
        PurePosixPath(v).parent.as_posix()
        for _t, v in children
        if isinstance(v, str)
    }
    if len(dirs) == 1 and dirs != {"."}:
        return dirs.pop()
    return re.sub(r"[^a-z0-9-]+", "-", title.lower()).strip("-")


def slug_of(md_path: str) -> str:
    """Site slug for a source page (``launch/README.md`` -> ``index``)."""
    stem = PurePosixPath(md_path).stem
    return "index" if stem.lower() in INDEX_STEMS else stem


def build_plan() -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    """src_rel -> site URL, and out_dir -> meta.json {title, pages}."""
    nav = yaml.safe_load(MKDOCS.read_text())["nav"]
    url_of: dict[str, str] = {}
    metas: dict[str, dict[str, object]] = {"": {"title": "Docs", "pages": []}}

    def walk(entries: list[tuple[str, object]], out_dir: str) -> None:
        for title, value in entries:
            if isinstance(value, list):
                children = nav_entries(value)
                folder = folder_for(title, children)
                joined = f"{out_dir}/{folder}" if out_dir else folder
                metas[out_dir]["pages"].append(folder)
                metas.setdefault(joined, {"title": title, "pages": []})
                walk(children, joined)
            else:
                src = value
                slug = slug_of(src)
                if slug == "index":
                    url = f"/docs/{out_dir}".rstrip("/") or "/docs"
                else:
                    url = f"/docs/{out_dir}/{slug}" if out_dir else f"/docs/{slug}"
                url_of[src] = url
                metas[out_dir]["pages"].append(slug)

    walk(nav_entries(nav), "")
    return url_of, metas


def split_code(text: str) -> list[tuple[str, str]]:
    """Split into [('prose'|'fence', chunk)] — fences kept with their markers."""
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    fence: list[str] | None = None
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                parts.append(("prose", "\n".join(buf)))
                buf = []
                fence = [line]
            else:
                buf.append(line)
        else:
            fence.append(line)
            if m and not m.group(1):
                parts.append(("fence", "\n".join(fence)))
                fence = None
    if fence is not None:
        parts.append(("fence", "\n".join(fence)))
    else:
        parts.append(("prose", "\n".join(buf)))
    return parts


def escape_prose(text: str) -> str:
    """Escape MDX-hostile characters outside inline code spans."""
    segs = re.split(r"(`[^`\n]*`)", text)
    out = []
    for i, seg in enumerate(segs):
        if i % 2:
            out.append(seg)
            continue
        seg = seg.replace("{", r"\{").replace("}", r"\}")
        seg = re.sub(r"<(?=\S)", "&lt;", seg)
        out.append(seg)
    return "".join(out)


def first_paragraph(text: str) -> str:
    """First substantial non-heading paragraph as plain text (<=160 chars)."""
    for block in re.split(r"\n\s*\n", text):
        line = block.strip()
        if not line or line.startswith(("#", "```", ">", "|", "-", "*", "<")):
            continue
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"[*_`~]", "", line)
        line = " ".join(line.split())
        if len(line) >= 25:
            return line[:157] + ("..." if len(line) > 160 else "")
    return ""


def module_source_link(mod: str) -> str:
    """Markdown line replacing an mkdocstrings directive."""
    tail = mod.removeprefix("trpc_rs_py").strip(".")
    path = "trpc_rs_py/__init__.py" if not tail else f"trpc_rs_py/{tail.replace('.', '/')}.py"
    return f"*Generated from docstrings — see [`{mod}`]({GITHUB_SRC}/{path}).*"


def resolve(path: str, src_rel: str) -> str:
    """Resolve a relative link target against the source file's dir."""
    import posixpath

    joined = posixpath.join(PurePosixPath(src_rel).parent.as_posix(), path)
    return posixpath.normpath(joined).removeprefix("./")


def convert_link(m: re.Match[str], src_rel: str, url_of: dict[str, str]) -> str:
    """Rewrite one markdown link/image target for the site."""
    bang, label, target = m.groups()
    if re.match(r"^[a-z]+:", target) or target.startswith("#"):
        return m.group(0)
    path, _, anchor = target.partition("#")
    frag = f"#{anchor}" if anchor else ""
    if path.endswith((".md", ".mdx")):
        resolved = resolve(path, src_rel)
        url = url_of.get(resolved)
        if url is None:
            print(f"sync: warning — unresolved link {target} in {src_rel}", file=sys.stderr)
            url = f"/docs/{PurePosixPath(resolved).with_suffix('')}"
        return f"{bang}[{label}]({url}{frag})"
    if bang == "!":
        return f"{bang}[{label}](/{resolve(path, src_rel)}{frag})"
    return f"{bang}[{label}]({GITHUB_BLOB}/{resolve(path, src_rel)}{frag})"


def extract_title(body: str) -> tuple[str, str]:
    """Remove the first prose ``# `` line; return (title, remaining_body)."""
    out: list[str] = []
    title = ""
    for kind, chunk in split_code(body):
        if kind != "prose" or title:
            out.append(chunk)
            continue
        lines = chunk.split("\n")
        kept = []
        for line in lines:
            m = H1_RE.match(line)
            if m and not title:
                title = m.group(1).strip()
            else:
                kept.append(line)
        out.append("\n".join(kept))
    return title, "\n".join(out)


def transform(md: str, src_rel: str, url_of: dict[str, str]) -> str:
    """Convert one docs/*.md file to MDX with frontmatter."""
    title, body = extract_title(md)
    if not title:
        title = PurePosixPath(src_rel).stem.replace("-", " ").title()

    out: list[str] = []
    for kind, chunk in split_code(body):
        if kind == "fence":
            head = FENCE_RE.match(chunk.split("\n", 1)[0])
            if head and head.group(1) == "mermaid":
                chart = "\n".join(chunk.split("\n")[1:-1])
                chart = chart.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                out.append(f"<Mermaid chart={{`{chart}`}} />")
            else:
                out.append(chunk)
            continue
        chunk = re.sub(
            r"^:::\s+(\S[^\n]*)(?:\n(?:[ \t]+[^\n]*)*)?",
            lambda m: module_source_link(m.group(1)),
            chunk,
            flags=re.M,
        )
        chunk = LINK_RE.sub(lambda m: convert_link(m, src_rel, url_of), chunk)
        out.append(escape_prose(chunk))
    body = "\n".join(out).strip() + "\n"

    desc = first_paragraph(body)
    desc_yaml = f"description: {json.dumps(desc)}\n" if desc else ""
    return f"---\ntitle: {json.dumps(title)}\n{desc_yaml}---\n\n{body}"


def copy_assets() -> None:
    """Copy non-Markdown files under docs/ into the site's public/."""
    for src in DOCS.rglob("*"):
        if src.is_file() and src.suffix.lower() not in {".md", ".py"}:
            rel = src.relative_to(DOCS)
            dest = PUBLIC / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def main() -> int:
    """Regenerate the whole site content tree; returns an exit code."""
    url_of, metas = build_plan()
    if CONTENT.exists():
        shutil.rmtree(CONTENT)
    CONTENT.mkdir(parents=True)
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir()

    written = 0
    for src in sorted(DOCS.rglob("*.md")):
        rel = src.relative_to(DOCS).as_posix()
        url = url_of.get(rel)
        if url is None:
            url = f"/docs/{PurePosixPath(rel).with_suffix('')}"
            print(f"sync: warning — {rel} not in mkdocs nav", file=sys.stderr)
        if url == "/docs" or PurePosixPath(rel).stem.lower() in INDEX_STEMS:
            rel_out = url.removeprefix("/docs/") if url != "/docs" else ""
            dest = CONTENT / rel_out / "index.mdx"
        else:
            dest = CONTENT / f"{url.removeprefix('/docs/')}.mdx"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(transform(src.read_text(), rel, url_of))
        written += 1

    for dir_rel, meta in metas.items():
        target = CONTENT / dir_rel if dir_rel else CONTENT
        target.mkdir(parents=True, exist_ok=True)
        (target / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    copy_assets()
    print(f"sync: wrote {written} pages + {len(metas)} meta.json under {CONTENT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
