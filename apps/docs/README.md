# trpc-rs-py docs site

Fumadocs (Next.js + MDX) app — the published documentation site.

**Do not edit `content/` or `public/` — they are generated.** The source of
truth is the MkDocs tree at [`../../docs`](../../docs), which `just ci` gates
on. Regenerate and build:

```bash
just site      # from the repo root: sync docs/ → MDX, then `pnpm build`
just site-dev  # sync, then `pnpm dev`
```

or directly: `uv run python ../../scripts/sync_docs_site.py && pnpm dev`.

The sync script converts `mkdocs.yml` nav into `meta.json`, `# Title` into
frontmatter, ```` ```mermaid ```` fences into the `<Mermaid>` component
(`components/mdx/mermaid.tsx`), relative `.md` links into `/docs/...` URLs,
and copies assets into `public/`.

Deployed on Vercel: https://trpc-rs-py.vercel.app/docs —
`pnpm dlx vercel --prod` from this directory redeploys.
