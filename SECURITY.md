# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| latest release (currently 0.7.x) | yes |
| older 0.x releases | no — upgrade to the latest |

While the project is pre-1.0, only the latest minor release receives
security fixes.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

- Email: security reports go to the maintainer via the contact listed on
  the GitHub profile (<https://github.com/Seyamalam>).
- Include: affected version(s), a minimal reproduction, the impact you
  believe is possible, and whether the issue is in the Rust core, the
  Python layer, an adapter, or the standalone server.
- Expected response: acknowledgement within 7 days; a fix or a published
  mitigation targets the next release. There is no bug bounty.

We ask that you give us reasonable time to fix an issue before public
disclosure. Credited disclosure is offered on request.

## Scope

In scope: the `trpc_rs_py` package, the bundled `trpc_rs_py._core`
extension, the `trpc-py` CLI, the standalone server, and the generated
artifacts (`.d.ts`, `zod`, OpenAPI, Python client stubs).

Out of scope: vulnerabilities in upstream dependencies (report those to
the upstream project — our dependency audits track their advisories),
attacks requiring local filesystem access, and denial of service against
development-only tooling (`trpc-py dev`, `/trpc/panel`), which is
documented as dev-only and production-disabled.

## Hardening checklist for deployments

- Run with `production=True` (or `TRPC_PY_PRODUCTION=1`) — fixed error
  envelopes, no stack traces, panel and dev tooling disabled.
- Keep the default `Limits` or set them tighter for your workload; see
  `docs/limits.md` and `docs/security/threat-model.md`.
- Put cookie-authenticated deployments behind `csrf_protect()` and use
  `cors()` with an explicit origin allowlist — both are deny-by-default.
- Serve behind TLS termination; the server emits no secrets itself but
  headers may carry credentials.
