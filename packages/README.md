# npm packages

TypeScript/JavaScript packages shipped alongside the Python wheel (MISSION.md
section 3). Versions move in lockstep with the workspace version via
`scripts/release.sh`.

- `@trpc-rs-py/link` — migration link (section 2.2): routes procedures under a
  chosen prefix (e.g. `py.*`) to the Python server, everything else to the Node
  server; plus a type helper merging the Node `AppRouter` and the generated
  Python router type. (Milestone 0.4.0.)

Standards (MISSION.md section 5): TypeScript `strict`, no `any` in public
types, TSDoc on public exports, prettier formatting, pnpm as package manager.
