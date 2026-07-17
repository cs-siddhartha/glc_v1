# Security Findings and Fixes

This document records the findings addressed during the Modal migration. An
invariant is the security or data-integrity condition that must remain true even
when the gateway is reachable from the public internet or scales on Modal.

| # | Finding | Broken invariant | Fix |
|---|---|---|---|
| 1 | Production documentation and gateway exposure | Production metadata and gateway routes must not be anonymously reachable. | Production disables `/docs`, `/redoc`, and `/openapi.json`. The Modal ASGI endpoint also requires Modal proxy authentication, while application routes enforce the installation bearer token. |
| 2 | Config disclosure (`/v1/status`, `/v1/providers`, `/v1/capabilities`) | Provider order, model selection, capabilities, and rate limits are tenant configuration and require authentication. | All configuration-bearing read routes call the shared installation-token validator before returning data. |
| 3 | Unauthenticated LLM abuse (`/v1/chat`) | No unauthenticated caller may execute or bill the data plane. | `/v1/chat` and the related chat, embedding, batch, and media routes require `Authorization: Bearer <install_token>` and reject a missing credential with HTTP 401. |
| 4 | SSRF through the image URL resolver | Server-side fetches may reach only explicitly approved public destinations, including after redirects and DNS resolution. | Image hosts must match `GLC_IMAGE_URL_ALLOWLIST`. The resolver permits only HTTP(S) on ports 80/443, rejects URL credentials, resolves and rejects every non-global IPv4 or IPv6 address, disables automatic redirects, and repeats the complete validation before every redirect hop. |
| 5 | Verbose upstream errors | Provider names, endpoints, credentials-related responses, and raw upstream bodies must remain in server-side diagnostics. | Provider failures are logged server-side and converted to generic client messages such as `upstream provider request failed` or `upstream service unavailable`. Image-fetch failures are handled the same way. |
| 6 | Unauthenticated usage and cost reads (`/v1/cost/by_agent`, `/v1/calls`) | Usage records belong to an authenticated tenant and must not be readable across tenant boundaries. | Both endpoints require the installation token. Ledger rows carry a stable tenant identifier derived from the token, and queries filter by that authenticated tenant before returning results. |
| 7 | Single function with no egress wall (A3) | Untrusted commands must not inherit unrestricted network access from the gateway process. | Untrusted commands run in short-lived Modal Sandboxes with bounded time, CPU, memory, output, and an explicit `outbound_domain_allowlist`. |
| 8 | Leak 1: shared process environment / one secret for the whole function (A4) | Adapter code must not be able to read another provider's credential from its process environment; a component may receive only the credential needed for its current operation. | Each credential-bearing provider adapter runs in its own Modal function with one provider-specific secret. The gateway and its channel-adapter stubs hold no provider secrets, so an environment read there cannot expose a provider key. Tool credentials are issued only to the selected short-lived Sandbox slot. |
| 9 | Leak 2: writable audit log | Adapter and provider code must not be able to erase or silently rewrite audit history, and persisted tampering must be detectable. | Only the gateway mounts the audit volume. SQLite triggers reject `UPDATE` and `DELETE`; every entry contains the previous entry's SHA-256 digest and its own digest; appends serialize under an immediate transaction; startup verifies the complete chain and refuses a modified store. |
| 10 | Non-reproducible image (A5) | A deployment must resolve to the reviewed base image and dependency graph, not newer packages selected at build time. | The Debian/Python base image is pinned by digest. Modal installs from `uv.lock` with frozen resolution, a pinned uv version, and development dependencies excluded. |
| 11 | Audit volume assumes one writer (A6) | SQLite audit, pairing, and usage files on a Modal Volume must never have multiple container writers, and acknowledged writes must be persisted. | The gateway function is capped at `max_containers=1`. Its ASGI wrapper explicitly commits the Modal Volume before final HTTP responses, WebSocket replies or closure, and lifespan shutdown completion. |

## Deployment note

The fixes are present in the workspace. The latest live deployment is pending
creation of the provider-scoped `glc-provider-gemini` secret in Modal's `main`
environment; the deployment intentionally does not fall back to the former
whole-function secret.
