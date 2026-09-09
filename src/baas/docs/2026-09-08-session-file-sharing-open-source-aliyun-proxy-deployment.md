# Session File Sharing — Open-Source Aliyun Proxy Deployment

**Date:** 2026-09-08
**Scope:** community tree only (`ocb-public/src/baas`). This document describes
the open-source aliyun deployment form added by phase 89: a pure-Python second
leg that replaces the enterprise image's nginx hop. Operators deploy the
community code with 8 environment variables, no nginx, no `FT_PROXY_HOST`.

# Scope and morphology split

Phase 89 keeps the two deployment morphologies side by side. The enterprise
image stays byte-untouched (9 variables + nginx); the open-source form is a
new, self-contained Python leg.

| Aspect | Open-source (this phase) | Enterprise (unchanged) |
|--------|--------------------------|------------------------|
| Second-leg forwarder | Python `OssStreamingProxy` (`plugins/file_transfer/_http_proxy.py`) + FastAPI wildcard router | tengine/nginx `proxy_pass` hop |
| Environment contract | 8 variables (strict `${VAR}` tenant overlay) | 9 variables (adds `FT_PROXY_HOST`, nginx-only) |
| Config entry | `configs/application-aliyun.yaml` (community) | enterprise overlay (byte-untouched) |
| Presigning backend | community `AliyunOssFileTransferBackend` via Selector key `real` | enterprise factory — resolved through the same community `real` key (89-03, A3) |
| Behavior guarantee | byte-identical backend port; A3 canary green | zero drift from this phase (D-89-06) |

# Route contract

- **Route:** `GET|PUT /api/v1/file-transfer-proxy/{key}?query` — a wildcard
  route mounted on the community app; the client PUTs/GETs object keys through
  this path, never against a raw OSS URL.
- **Signature trinity, byte-for-byte:** the raw path (percent-encoded form)
  and the raw query string are read from the ASGI `raw_path`/`query_string`
  byte surfaces and forwarded untouched — never decoded, never re-encoded
  (OSS V1 signs path + query + Content-Type; any decode/re-encode breaks
  verification into 403 `SignatureDoesNotMatch`).
- **Signed-header passthrough:** `Content-Type`, `Content-MD5` and every
  `x-oss-*` header pass through verbatim; hop-by-hop headers (`connection`,
  `keep-alive`, `te`, `trailers`, `transfer-encoding`, `upgrade`,
  `proxy-authorization`, …) are stripped; `Content-Length` is relayed as sent.
- **Host derivation:** `Host` is recomputed server-side as
  `{bucket}.{endpoint-host}` — the client's Host is never trusted.
- **Response relay:** upstream status and body are relayed verbatim;
  hop-by-hop response headers are stripped; `X-Accel-Buffering: no` is added
  so no outer nginx layer ever buffers to disk.
- **Error ladder:** 404 `FILE_PROXY_BAD_PATH` (the raw path failed the proxy
  prefix guard); 405 (any method other than GET/PUT, rejected by the route
  whitelist); 502 `FILE_PROXY_UPSTREAM_ERROR` (upstream unreachable before any
  status line was committed); 503 `SESSION_FILE_TRANSFER_PROXY_UNAVAILABLE`
  (endpoint/bucket unconfigured — fail-closed, never an open relay); upstream
  4xx/5xx are relayed verbatim (e.g. OSS 403/404 engine replies).
- **Mid-stream truncation:** if the upstream dies mid-transfer, the client
  receives the bytes so far and a clean close — the response status line was
  already committed, so no synthetic 500 is possible; the abort is logged.

# Environment variables

The complete open-source contract is exactly these 8 variables. The
enterprise-only `FT_PROXY_HOST` is NOT part of it (that variable exists only
for the enterprise nginx leg).

| Variable | Consumer | Example |
|----------|----------|---------|
| `BAAS_DEPLOY_TENANT` | ConfigLoader tenant layer → loads `configs/application-aliyun.yaml`; bootstrap factory branch selector | `aliyun` |
| `FT_OSS_ENDPOINT` | `file_transfer_oss_aliyun.endpoint` — proxy upstream and server-side OSS API | `https://oss-cn-hangzhou-internal.aliyuncs.com` |
| `FT_OSS_EXTERNAL_ENDPOINT` | `file_transfer_oss_aliyun.external_endpoint` — endpoint used to sign presigned URLs | `https://oss-cn-hangzhou.aliyuncs.com` |
| `FT_OSS_BUCKET` | `file_transfer_oss_aliyun.bucket_name` — upstream `Host: {bucket}.{endpoint-host}` | `session-files` |
| `FT_OSS_STAGING_ROOT` | `file_transfer_oss_aliyun.staging_root_path` — staging object-key prefix | `staging` |
| `FT_OSS_ACCESS_KEY` | bootstrap factory, `os.environ` direct read (env-only — never YAML, never a MIST/plugin secret) | `LTAI5t...` |
| `FT_OSS_SECRET_KEY` | bootstrap factory, `os.environ` direct read (env-only — must never be logged) | `(secret)` |
| `FT_PROXY_BASE_URL` | `session_file_url_proxy.proxy_base_url` — base for projecting OSS URLs onto the BaaS domain | `https://baas.example.com` |

Fail-closed startup: the overlay's five placeholders are strict `${VAR}`
(no `:-default`), so any missing mapped variable raises `KeyError` at config
load, before any service starts. A missing `FT_OSS_ACCESS_KEY` /
`FT_OSS_SECRET_KEY` raises `ConfigError` from the factory. With
`BAAS_DEPLOY_TENANT` unset, the overlay is unreachable and main-site (stub)
semantics apply.

# Client requirements

- Call exactly the URL the service projected (BaaS domain, `/api/v1/file-transfer-proxy/...`), replaying the query component **byte-for-byte** — no reordering, no re-encoding, no dict rebuild.
- **Content-Type signature contract:** the Content-Type sent on the request must equal, byte-for-byte, the value that was signed into the presigned URL
  (`generate_upload_url` signs `headers={"Content-Type": ct}`). Any difference
  — trimming, charset suffixing, normalization — is rejected by OSS as 403
  `SignatureDoesNotMatch`. `Content-MD5`, when present in the signed URL, must
  ride unchanged as well.
- Multipart uploads: every per-part PUT carries its own presigned query
  (`uploadId` + `partNumber`) through the proxy path; each part's query is
  replayed verbatim.
- Unsigned or tampered requests fail at OSS with 403. The proxy applies no
  authorization of its own — ingress authentication is the gateway's job.

# Operational notes

- **No path semantic validation by design.** The proxy treats the captured
  raw path as an opaque object key; any "validation" would rewrite bytes and
  break the signature. Security rests on OSS-side signature verification.
- **Timeouts:** the proxy's httpx client is pinned at connect/pool = 10 s and
  read/write = 600 s (never the httpx 5 s default, which would sever large
  transfers). Gateway timeouts and request-body caps must sit above these —
  see the ingress four-piece (auth passthrough, request-body cap, timeout
  above 600 s, CORS for the proxy path).
- **Streaming:** both legs stream (upload via `request.stream()`, download via
  `StreamingResponse`) — object bodies are never buffered to disk.
- **crcmod:** oss2 depends on crcmod, whose C extension may lack a wheel on
  some platforms; the pure-Python fallback (`crcmod.predefined`) keeps oss2
  importable — verify at first deploy.
- **Logging:** AK/SK are read from the process environment outside any config
  section and must never appear in config summaries — audit the boot log once
  after deployment.

# Changelog

- 2026-09-08: initial doc for the open-source aliyun proxy deployment form
  (phase 89).