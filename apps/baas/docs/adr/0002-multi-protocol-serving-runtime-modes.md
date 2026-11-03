# ADR-0002: Multi-protocol serving with dual runtime modes

## Status

accepted

## Date

2026-10-26

## Applies to

all

## Context

The legacy enterprise README advertises "Web, MCP, and SOFA RPC" service
types, but no MCP server implementation exists in either edition's tree
today; the only MCP references are aspirational comments. What exists is one
web transport plus a pluggable RPC runner seam, selected by a runtime mode
flag. `main.py` documents: "Unified entry point — selects SOFA or bare mode
via `--mode`" and "Enterprise registers 'sofa' runner; community registers
'bare' runner."

## Decision

Serve all traffic through one FastAPI web adapter in the community platform
(`adapters/web/`), and treat additional protocols as runner *plugins* rather
than as duplicated servers:

- runtime modes are explicit: `--mode bare` (standalone FastAPI, default for
  community single-box) or `--mode sofa` (behind the MOSN/Layotto service
  mesh, enterprise);
- the community build ships the `bare` runner plugin; the enterprise overlay
  registers the `sofa` runner plugin (`runner/sofa/_plugin.py`);
- core services (`core/service/`) stay protocol-agnostic and are never wired
  to a specific transport (ADR-0001);
- MCP is a documented aspiration in `core/service/bot_run/_runner.py`
  ("Web、RPC、MCP 等多种入口复用") and legacy docs, not a present capability;
  any future MCP server must be added as another runner plugin, not as a
  second server stack with its own business logic.

## Consequences

**Positive:** one protocol surface to secure, test, and document; adding a
protocol (e.g., MCP) means adding a plugin and re-using every core service;
bare mode keeps community development dependency-free of the mesh.

**Negative:** protocol parity claims in legacy docs (`README.md`, legacy
ARCHITECTURE sections) overstate what ships today (no MCP).

**Risks:** future MCP demand forcing a second `servers/`-style stack inside
core; mitigated by this ADR naming runner plugins as the only sanctioned
extension point.

## Evidence

- community: `src/secbaas/community/main.py` — `--mode bare|sofa`, "Enterprise registers 'sofa' runner; community registers 'bare' runner"
- community: `src/secbaas/community/adapters/web/` — the web adapter
- community: `src/secbaas/community/core/service/bot_run/_runner.py` — MCP aspiration comment
- enterprise: `src/secbaas/enterprise/plugins/runner/sofa/_plugin.py` — SOFA runner plugin
- enterprise: `README.md` — legacy "Web、MCP、SOFA RPC" claim (history; superseded on MCP)