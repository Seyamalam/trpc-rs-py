# npm packages

TypeScript/JavaScript packages shipped alongside the Python wheel (MISSION.md
section 3). Versions move in lockstep with the workspace version via
`scripts/release.sh`.

- `@trpc-rs-py/link` (`packages/link`) — migration link (section 2.2, ADR
  0008): `migrationLink` routes operations to Python by `py.` prefix or an
  explicit `procedures` list (subscriptions included, paths unrewritten);
  `MergedAppRouter<TNode, TPy>` merges the Node `AppRouter` and the generated
  Python router type. Runnable demo: `examples/migrate-from-node`.

Standards (MISSION.md section 5): TypeScript `strict`, no `any` in public
types, TSDoc on public exports, prettier formatting, pnpm as package manager.
