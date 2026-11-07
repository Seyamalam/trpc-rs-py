# Security Policy

## Supported versions

Pre-release: only the latest `main` is supported.

| Version | Supported |
| --- | --- |
| latest `main` | yes |
| older tags | no |

## Reporting a vulnerability

Do **not** open a public issue for a security problem.

Email **seyamalam41@gmail.com** with:

- A description of the issue and its impact.
- Steps to reproduce (a minimal example is best).
- Any suggested mitigation (optional).

You will get an acknowledgment within 72 hours. Fixes are released as soon as
feasible; reporters are credited unless they prefer anonymity.

## Security model summary

The full threat model lives in `docs/security/threat-model.md` (written during
milestone 0.8.0). Key guarantees from the first public release:

- Production mode never leaks stack traces or internal error messages.
- Enforced, configurable limits: body size, batch size, JSON depth, procedure
  timeout, subscription count per connection, SSE buffer size.
- Queries are side-effect free; mutations only over POST.
- CORS is deny-by-default with an explicit allowlist.
- The playground and REST docs are off in production unless explicitly enabled.
