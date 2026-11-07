# Templates

Scaffolds for `trpc-py init` (MISSION.md section 2.8). The main template —
Next.js + FastAPI monorepo (pnpm + uv) with tRPC, react-query, live type sync,
one query, one mutation, and one streaming chat example — is required for the
0.3.0 launch.

Other templates: `fastapi-only`, `flask-only`, `django-only`,
`add-to-existing-node-app`.

Target: from `pip install` to first typed call in the browser in under
2 minutes. Each template has a `smoke.sh` run by the local CI gate from a
clean directory.
