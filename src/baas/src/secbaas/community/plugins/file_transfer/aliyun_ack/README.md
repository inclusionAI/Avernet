# Aliyun ACK Session File URL Projector

## Context Boundary

```yaml
purpose: Rewrites session-file presigned OSS URLs to the BaaS-domain /api/v1/file-transfer-proxy/ prefix for the ALIYUN_ACK deploy tenant.
provides:
  - AliyunAckSessionFileUrlProjector
consumes:
  - SessionFileUrlProjector                    # spi protocol implemented here
  - SessionFileTransferProxyUnavailableError    # api.session_file_sharing error contract
  - secbaas.community.logger
internal_dependencies:
  - secbaas.community.spi.file_transfer
  - secbaas.community.api.session_file_sharing
```

### Change impact

Changing the projection transform alters the shape of every URL handed to
ALIYUN_ACK clients (upload-url SINGLE, MULTIPART per-part, and share-link).
Clients PUT/GET those URLs through the BaaS-domain nginx proxy, so the
`/api/v1/file-transfer-proxy/` prefix must stay in sync with the nginx
rewrite rule, and the query must remain byte-preserved for OSS signature
verification — a regression here surfaces as client-side 403s at the proxy.
Main-site deployments never exercise this class (the DI Selector serves the
Noop identity by default); the D-01 tenant guard additionally keeps a
misconfigured main site on passthrough with a WARNING.