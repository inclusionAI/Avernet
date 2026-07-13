#!/usr/bin/env python3
"""Adapters HTTP endpoint coverage report (e2e).

End-point-level coverage (not line coverage): of all HTTP routes registered in
bcs-http's router.rs, which ones does the e2e suite actually exercise?

Two inputs:
  1. The full registered endpoint set — parsed from router.rs. Verified by
     inspection (build_api_routes is the single, unconditional, no-nest
     registration site; there is no OpenAPI/utoipa spec to read instead).
  2. The set of endpoints actually hit — the instrumented bcs logs every
     request as `[→BCS] METHOD PATH` (see debug_middleware in
     crates/bootstrap/bcs/src/server.rs, gated on BCS_DEBUG=true; e2e_coverage.sh
     exports it). Path params in hits are concrete (a real bot uuid), registered
     templates have `{param}` placeholders, so hits are matched to templates.

Self-checks (so the parsed set is verified, not trusted):
  - over-count: a parsed (method, template) is only counted if it is NOT axum's
    plain 404 for a dummy path (optional --probe; requires a running bcs). A
    parsed route that 404s means a parse bug / a route that does not really
    exist — surfaced, not silently counted.
  - under-count: any hit path that matches NO parsed template is listed as
    "unmatched hit" — either a parse miss or a route registered elsewhere.
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

# method helpers allowed in axum routing::get/post/put/patch/delete. `any` is
# excluded on purpose — neither shows up here (all routes use the typed helpers).
METHODS = ("get", "post", "put", "patch", "delete")
# debug_middleware emit: eprintln!("\x1b[2m[→BCS] {} {}\x1b[0m", method, path).
# The hit line begins at line start with `[→BCS]` (preceded only by the dim ANSI
# escape). CRUCIAL: anchor to the leading `[→BCS]`, NOT to a bare `BCS]` — the
# bcs_ws dispatcher also logs lines like `📥 [BCS] 收到 Bot 事件 ...`, whose
# `BCS]` would otherwise be mis-parsed as (method="收到", path="Bot"). The arrow
# `→` (U+2192) distinguishes the middleware hit line from those dispatcher logs.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Line-start `[→BCS]` after optional ANSI escapes.
HIT_RE = re.compile(r"(?:\x1b\[[0-9;]*[A-Za-z])*\[→BCS\]\s+(\S+)\s+(\S+)")


@dataclass(frozen=True)
class Endpoint:
    method: str          # GET/POST/... uppercase
    path: str            # registered template, e.g. /groups/{id}/members
    handler: str         # e.g. routes::groups::add_group_member
    line: int            # router.rs line where .route( begins

    def key(self):
        return (self.method, self.path)


@dataclass
class Coverage:
    endpoints: list  # all parsed (Endpoint)
    covered: set = field(default_factory=set)        # set of (method, path)
    unmatched_hits: set = field(default_factory=set)  # set of (method, raw_path)


def parse_router(router_path: str) -> list:
    """Parse every .route("PATH", METHOD(h)...) into Endpoint list.

    Handles single-line and multi-line .route(...) blocks and chained
    .method(h).method(h) forms. A block yields one Endpoint per method helper
    found in it; all share the block's single string-literal path.
    """
    with open(router_path, encoding="utf-8") as f:
        src = f.read()

    # Strip C-style line and block comments BEFORE parsing so commented-out
    # routes (e.g. `// .route(...)`) are not picked up as active endpoints.
    # router.rs has no `//` or `/* */` sequences inside its path string
    # literals, so a plain regex strip is safe here.
    src = re.sub(r"//.*", "", src)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)

    # Work over a token span of the whole file. We split on `.route(` and parse
    # each segment up to the matching `)` at the router-builder nesting level.
    # Simpler & robust than a full Rust parser for this constrained DSL.
    endpoints = []
    for m in re.finditer(r"\.route\(\s*", src):
        start = m.end()  # just after ".route( "
        line = src.count("\n", 0, m.start()) + 1
        # Find the closing paren matching this .route( — depth-aware, respecting
        # string literals so a ')' inside a path string can't end the block.
        depth = 1
        i = start
        in_str = False
        while i < len(src):
            c = src[i]
            if in_str:
                if c == "\\":
                    i += 2
                    continue
                if c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        block = src[start:i]

        # The path is the first string literal in the block.
        pm = re.search(r'"((?:[^"\\]|\\.)*)"', block)
        if not pm:
            continue  # not a string-path .route (e.g. a different overload) — skip
        path = pm.group(1)

        # Methods: each bare word get/post/put/patch/delete followed by '(' that
        # introduces a handler. Match word-boundaried helper calls.
        for mm in re.finditer(r"\b(get|post|put|patch|delete)\s*\(", block):
            method = mm.group(1).upper()
            # Grab the handler token inside that call's parens (up to first ','
            # or ')'), for human-readable display only.
            hstart = mm.end()
            hend = _matching_paren(block, hstart - 1)
            inside = block[hstart:hend].strip()
            handler = re.split(r"[,\s]", inside, maxsplit=1)[0]
            endpoints.append(Endpoint(method, path, handler, line))
    return endpoints


def _matching_paren(s, open_idx):
    """Return index of the ')' matching '(' at open_idx, respecting strings."""
    depth = 0
    i = open_idx
    in_str = False
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return len(s)


def parse_hits(log_path: str):
    """Yield (method_upper, raw_path_no_query) for every [→BCS] line in bcs.log.

    Anchored to line-start `[→BCS]` (after stripping ANSI) so the bcs_ws
    dispatcher lines `📥 [BCS] 收到 Bot 事件 ...` are NOT mis-parsed as hits.
    """
    hits = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                clean = ANSI_RE.sub("", raw)
                m = HIT_RE.match(clean)
                if not m:
                    continue
                method = m.group(1).upper()
                path = m.group(2).split("?", 1)[0]  # strip query
                hits.append((method, path))
    except FileNotFoundError:
        pass
    return hits


def path_segments(path):
    return [seg for seg in path.split("/") if seg != ""]


def build_template_matchers(endpoints):
    """For each (method), return a list of (segments, original_path) tuples,
    literal segments before param segments so the most-specific match wins per
    axum. Carries the original registered path template so match_hit can return
    it verbatim instead of reconstructing it (which would mis-handle templates
    with a trailing/missing slash)."""
    by_method = {}
    for ep in endpoints:
        by_method.setdefault(ep.method, []).append((path_segments(ep.path), ep.path))
    for m in by_method:
        # Sort: literal segments beat param segments; longer beats shorter.
        # A literal segment is one not wrapped in {}.
        def key(item):
            segs, _orig = item
            score = []
            for s in segs:
                is_param = s.startswith("{") and s.endswith("}")
                score.append(0 if is_param else 1)
            # per-segment specificity first, then total length
            return (score, len(segs))
        by_method[m].sort(key=key, reverse=True)
    return by_method


def match_hit(method, raw_path, matchers):
    """Return the registered path template a concrete hit path maps to, or None.

    Matches segment-by-segment: a literal segment must equal; a {param} segment
    matches any single (non-empty) concrete segment. No multi-segment wildcards
    exist in this router, so depth must be equal. Most-specific-first ordering
    picks the right template when several could match (e.g. /groups/join/{token}
    vs a hypothetical /groups/{id}/...).
    """
    segs = path_segments(raw_path)
    for tmpl, original_path in matchers.get(method, []):
        if len(tmpl) != len(segs):
            continue
        ok = True
        for ts, cs in zip(tmpl, segs):
            if ts.startswith("{") and ts.endswith("}"):
                if cs == "":
                    ok = False
                    break
            elif ts != cs:
                ok = False
                break
        if ok:
            return original_path
    return None


def probe_overcount(endpoints, base_url, verbose=False):
    """Optionally ping each parsed endpoint with a dummy path; return the subset
    that returns axum's plain 404 (= route does not actually exist = parse error).

    We tell plain-404 apart from handler-level 404 by the response body: axum's
    default NotFound returns an empty body, while handlers return JSON errors.
    Requires a running, non-instrumented bcs (it is just curl). Disabled unless
    --probe is passed.
    """
    import urllib.request
    import urllib.error

    real = []
    fake = []
    for ep in endpoints:
        # Replace each {param} with a dummy value.
        probe_path = re.sub(r"\{[^}]+\}", "x", ep.path)
        url = f"{base_url.rstrip('/')}{probe_path}"
        req = urllib.request.Request(url, method=ep.method)
        # Give a minimal body for body-expecting verbs so we test routing, not
        # the body parser (a 4xx other than 404 still proves the route exists).
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                real.append(ep)  # got a non-error response
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code == 404 and not body.strip():
                fake.append(ep)
            else:
                real.append(ep)  # handler-level 4xx/5xx => route exists
        except Exception:
            real.append(ep)  # network/server hiccup: assume real, don't prune
    if verbose and fake:
        sys.stderr.write(
            "probe: %d parsed route(s) returned plain 404 (ignored as over-count):\n"
            % len(fake))
        for ep in fake:
            sys.stderr.write("  %s %s  (router.rs:%d)\n" % (ep.method, ep.path, ep.line))
    return real, fake


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def in_ci():
    """True under GitHub Actions (matches e2e_cov_gate.py / cov_gate.py)."""
    return os.environ.get("CI") == "true" and os.environ.get("GITHUB_ACTIONS") == "true"


def gh_notice(msg):
    """Emit a GitHub Actions ::notice:: workflow annotation in CI.

    The message body is what GitHub shows; the '::notice::' prefix only turns it
    into a notice annotation. We never use printf-style '%' in the body (the
    caller passes a fully-formatted string), so there is nothing for the workflow
    command parser to misinterpret.
    """
    if in_ci():
        sys.stdout.write("::notice::" + msg + "\n")


def gh_error(msg):
    """Emit a GitHub Actions error annotation in CI."""
    if in_ci():
        sys.stdout.write("::error::" + msg + "\n")


def build_summary(endpoints, cov, hits, seen_hits, args):
    """Return (summary_lines, per_method_totals, covered_eps, uncov_eps, pct).
    summary_lines is the short human summary (no per-endpoint detail)."""
    total = len(endpoints)
    cov_n = len(cov.covered)
    pct = (cov_n / total * 100.0) if total else 0.0

    by_method = {}
    for e in endpoints:
        by_method.setdefault(e.method, 0)
        by_method[e.method] += 1
    cov_by_method = {m: 0 for m in by_method}
    for (m, _p) in cov.covered:
        cov_by_method[m] = cov_by_method.get(m, 0) + 1

    lines = []
    lines.append("== adapters endpoint coverage ==")
    lines.append("router source  : %s" % args.router)
    lines.append("hit log        : %s" % (args.log or "(none — coverage will be 0%)"))
    lines.append("parsed routes  : %d endpoints (%d HTTP paths, %d methods)"
                 % (len(endpoints),
                    len({e.path for e in endpoints}),
                    len({e.method for e in endpoints})))
    lines.append("hit invocations: %d raw (%d distinct method+path)"
                 % (len(hits), len(seen_hits)))
    lines.append("")
    lines.append("Endpoint coverage: %d / %d  (%.1f%%)" % (cov_n, total, pct))
    lines.append("")
    lines.append("By method:")
    for m in sorted(by_method):
        c, t = cov_by_method.get(m, 0), by_method[m]
        lines.append("  %-7s %2d / %2d  (%5.1f%%)" % (m, c, t, c / t * 100.0 if t else 0))

    if cov.unmatched_hits:
        lines.append("")
        lines.append("Unmatched hits (hit a path matching NO parsed template) — %d:"
                     % len(cov.unmatched_hits))
        for m, p in sorted(cov.unmatched_hits):
            lines.append("  ? %-6s %s" % (m, p))

    covered_eps = sorted(
        [e for e in endpoints if e.key() in cov.covered],
        key=lambda e: (e.path, e.method))
    uncov_eps = sorted(
        [e for e in endpoints if e.key() not in cov.covered],
        key=lambda e: (e.path, e.method))

    per_method = sorted(
        ((m, cov_by_method.get(m, 0), by_method[m]) for m in by_method),
        key=lambda x: x[0])
    return lines, per_method, covered_eps, uncov_eps, pct


def build_full_txt(summary_lines, per_method, covered_eps, uncov_eps, cov):
    """Full text report = the short summary + per-endpoint covered/uncovered
    detail. Written to the .txt file; not printed to stdout."""
    lines = ["=" * 72, "Adapters HTTP endpoint coverage (e2e)", "=" * 72]
    lines.extend(summary_lines[1:])  # drop the "== adapters ==" header (replaced above)
    lines.append("")
    lines.append("Covered endpoints (%d):" % len(covered_eps))
    for e in covered_eps:
        lines.append("  ✓ %-6s %-44s  %s" % (e.method, e.path, e.handler))
    lines.append("")
    lines.append("Uncovered endpoints (%d):" % len(uncov_eps))
    for e in uncov_eps:
        lines.append("  ✗ %-6s %-44s  %s  (router.rs:%d)"
                     % (e.method, e.path, e.handler, e.line))
    if cov.unmatched_hits:
        lines.append("")
        lines.append("Unmatched hits (hit a path matching NO parsed template) — %d:"
                     % len(cov.unmatched_hits))
        lines.append("  (these mean either a parse miss or a route registered "
                     "outside router.rs)")
        for m, p in sorted(cov.unmatched_hits):
            lines.append("  ? %-6s %s" % (m, p))
    lines.append("")
    return "\n".join(lines) + "\n"


def build_xml(summary_lines, per_method, covered_eps, uncov_eps, args, endpoints, cov, hits, seen_hits):
    """Structured XML report: overall coverage, by-method breakdown, and the
    full covered/uncovered endpoint lists."""
    total = len(endpoints)
    cov_n = len(covered_eps)
    pct = (cov_n / total * 100.0) if total else 0.0
    uncov_n = len(uncov_eps)

    out = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append('<endpointCoverage>')
    out.append('  <meta>')
    out.append('    <routerSource>%s</routerSource>' % xml_escape(args.router))
    out.append('    <hitLog>%s</hitLog>' % xml_escape(args.log or ""))
    out.append('    <parsedEndpoints>%d</parsedEndpoints>' % total)
    out.append('    <parsedPaths>%d</parsedPaths>'
               % len({e.path for e in endpoints}))
    out.append('    <parsedMethods>%d</parsedMethods>'
               % len({e.method for e in endpoints}))
    out.append('    <hitInvocations>%d</hitInvocations>' % len(hits))
    out.append('    <distinctHits>%d</distinctHits>' % len(seen_hits))
    out.append('  </meta>')

    out.append('  <overall covered="%d" total="%d" uncovered="%d" percent="%.1f"/>'
               % (cov_n, total, uncov_n, pct))

    out.append('  <byMethod>')
    for m, c, t in per_method:
        p = (c / t * 100.0) if t else 0.0
        out.append('    <method name="%s" covered="%d" total="%d" percent="%.1f"/>'
                   % (xml_escape(m), c, t, p))
    out.append('  </byMethod>')

    def ep_el(e, covered):
        return ('    <endpoint method="%s" path="%s" handler="%s" line="%d" covered="%s"/>'
                % (xml_escape(e.method), xml_escape(e.path),
                   xml_escape(e.handler), e.line,
                   "true" if covered else "false"))

    out.append('  <covered count="%d">' % cov_n)
    for e in covered_eps:
        out.append(ep_el(e, True))
    out.append('  </covered>')

    out.append('  <uncovered count="%d">' % uncov_n)
    for e in uncov_eps:
        out.append(ep_el(e, False))
    out.append('  </uncovered>')

    out.append('  <unmatchedHits count="%d">' % len(cov.unmatched_hits))
    for m, p in sorted(cov.unmatched_hits):
        out.append('    <hit method="%s" path="%s"/>'
                   % (xml_escape(m), xml_escape(p)))
    out.append('  </unmatchedHits>')

    out.append('</endpointCoverage>')
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description="adapters HTTP endpoint coverage report")
    here = os.path.dirname(os.path.abspath(__file__))
    bcs = os.path.dirname(here)
    ap.add_argument("--router", default=os.path.join(
        bcs, "crates/adapters/http/bcs-http/src/router.rs"),
        help="path to bcs-http router.rs")
    ap.add_argument("--log", help="path to singlebox bcs.log with BCS_DEBUG hits")
    ap.add_argument("--out-txt", help="write full text report (with per-endpoint detail) here")
    ap.add_argument("--out-xml", help="write structured XML report here")
    ap.add_argument("--probe", action="store_true",
                    help="live-probe each parsed route against --probe-base to drop over-counts")
    ap.add_argument("--probe-base", default="http://127.0.0.1:21000",
                    help="base URL for --probe")
    ap.add_argument("--min", type=float, default=0.0,
                    help="minimum endpoint coverage %% (0 = report only)")
    args = ap.parse_args()

    if args.min < 0 or args.min > 100:
        ap.error("--min must be between 0 and 100")

    endpoints = parse_router(args.router)
    if not endpoints:
        sys.stderr.write("ERROR: parsed 0 endpoints from %s\n" % args.router)
        sys.exit(2)

    if args.probe:
        endpoints, fake = probe_overcount(endpoints, args.probe_base, verbose=True)

    cov = Coverage(endpoints=endpoints)

    hits = parse_hits(args.log) if args.log else []
    matchers = build_template_matchers(endpoints)
    seen_hits = set()
    for method, raw_path in hits:
        seen_hits.add((method, raw_path))
        tpl = match_hit(method, raw_path, matchers)
        if tpl:
            cov.covered.add((method, tpl))
        else:
            cov.unmatched_hits.add((method, raw_path))

    summary_lines, per_method, covered_eps, uncov_eps, pct = build_summary(
        endpoints, cov, hits, seen_hits, args)

    # stdout: short summary only — no per-endpoint detail.
    summary = "\n".join(summary_lines) + "\n"
    written = []
    if args.out_txt:
        full_txt = build_full_txt(summary_lines, per_method, covered_eps, uncov_eps, cov)
        with open(args.out_txt, "w", encoding="utf-8") as f:
            f.write(full_txt)
        written.append(args.out_txt)
    if args.out_xml:
        xml = build_xml(summary_lines, per_method, covered_eps, uncov_eps, args,
                        endpoints, cov, hits, seen_hits)
        with open(args.out_xml, "w", encoding="utf-8") as f:
            f.write(xml)
        written.append(args.out_xml)

    # GitHub Actions: surface the overall endpoint coverage as a notice annotation
    # (e.g. "Overall endpoint coverage 35% (35 / 100)"). Report-only, never gates.
    # Emitted before the human summary so it appears as a top-of-step annotation.
    total_eps = len(endpoints)
    covered_n = len(covered_eps)
    gh_notice("Overall endpoint coverage %d%% (%d / %d)"
              % (int(round(pct)), covered_n, total_eps))

    if written:
        # Single "Report written to ..." line listing every artifact, then the
        # short summary (without per-endpoint detail).
        from_list = " and ".join(written)
        sys.stdout.write("Report written to %s\n" % from_list)
        sys.stdout.write("--- summary ---\n")
        # summary_lines[0] is the "== adapters endpoint coverage ==" header;
        # drop it since we already emitted the "Report written" / "--- summary ---" framing.
        for ln in summary_lines[1:]:
            sys.stdout.write(ln + "\n")
    else:
        sys.stdout.write(summary)

    if args.min > 0:
        if pct + 1e-9 < args.min:
            message = ("Adapter endpoint coverage %.1f%% (%d / %d) is below "
                       "the required %.1f%%" %
                       (pct, covered_n, total_eps, args.min))
            gh_error(message)
            if not in_ci():
                sys.stderr.write("FAIL: " + message + "\n")
            sys.exit(1)
        gh_notice("Adapter endpoint coverage gate passed: %.1f%% (%d / %d)"
                  % (pct, covered_n, total_eps))


if __name__ == "__main__":
    main()
