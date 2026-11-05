use super::{BcsClient};
use anyhow::{Context, Result, anyhow};

impl BcsClient {
    // ========================================================================
    // Session File Workspace
    // ========================================================================

    /// Prepare a file upload. POST /sessions/{sid}/files
    pub async fn prepare_session_file(
        &self,
        sid: &str,
        file_name: &str,
        size: u64,
        mime: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/files",
            self.base_url,
            urlencoding::encode(sid)
        );
        let body = serde_json::json!({ "file_name": file_name, "size": size, "mime_type": mime });
        let resp = self
            .add_auth(self.http_client.post(&url).json(&body))
            .send()
            .await
            .context("prepare session file")?;
        Self::ensure_success(resp, "prepare session file").await
    }

    /// PUT bytes to an upload_url. If the upload_url host != BCS base_url
    /// host, do NOT attach Authorization (backend presigned URL self-authenticates).
    /// `content_type` is the MIME type negotiated at prepare time so the blob is
    /// stored with the same content type the backend reserved at `upload-url`.
    pub async fn put_session_file_bytes(
        &self,
        upload_url: &str,
        bytes: reqwest::Body,
        content_type: &str,
    ) -> Result<()> {
        let source = reqwest::Url::parse(&self.base_url)?;
        let target = reqwest::Url::parse(upload_url)?;
        if !matches!(target.scheme(), "http" | "https") || target.host_str().is_none()
            || !target.username().is_empty() || target.password().is_some() {
            return Err(anyhow!("Invalid upload URL"));
        }
        let cross_host = source.origin() != target.origin();
        let mut req = self.http_client
            .put(upload_url)
            .header(reqwest::header::CONTENT_TYPE, content_type)
            .body(bytes);
        if !cross_host {
            req = self.add_auth(req);
        }
        let resp = req.send().await.map_err(reqwest::Error::without_url).context("put session file bytes")?;
        Self::ensure_success_status(resp, "put session file bytes").await
    }

    /// Complete a file upload. POST /sessions/{sid}/files/{id}/complete
    pub async fn complete_session_file(
        &self,
        sid: &str,
        file_id: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/files/{}/complete",
            self.base_url,
            urlencoding::encode(sid),
            file_id
        );
        let resp = self
            .add_auth(self.http_client.post(&url).json(&serde_json::json!({})))
            .send()
            .await
            .context("complete session file")?;
        Self::ensure_success(resp, "complete session file").await
    }

    /// Infer a MIME type from a file name's extension. Covers common upload
    /// types (text, JSON/HTML/XML, PDF, images, audio/video, archives); unknown
    /// or missing extensions return `None` so callers fall back to the generic
    /// `application/octet-stream`.
    fn guess_mime_from_extension(file_name: &str) -> Option<String> {
        let ext = std::path::Path::new(file_name)
            .extension()?
            .to_str()?
            .to_ascii_lowercase();
        Some(
            match ext.as_str() {
                "txt" => "text/plain",
                "md" => "text/markdown",
                "csv" => "text/csv",
                "json" => "application/json",
                "xml" => "application/xml",
                "pdf" => "application/pdf",
                "zip" => "application/zip",
                "gz" => "application/gzip",
                "tar" => "application/x-tar",
                "html" | "htm" => "text/html",
                "css" => "text/css",
                "js" => "text/javascript",
                "png" => "image/png",
                "jpg" | "jpeg" => "image/jpeg",
                "gif" => "image/gif",
                "webp" => "image/webp",
                "svg" => "image/svg+xml",
                "wav" => "audio/wav",
                "mp3" => "audio/mpeg",
                "mp4" => "video/mp4",
                "bin" => "application/octet-stream",
                _ => return None,
            }
            .to_string(),
        )
    }

    /// Whether a MIME type is textual and therefore eligible for a `charset`
    /// parameter. Binary types (images other than SVG, archives, audio/video)
    /// never get a charset — appending one would mislabel the octet payload.
    fn is_text_mime(mime: &str) -> bool {
        // Normalize to the type before any `;` parameter for the check.
        let base = mime
            .split(';')
            .next()
            .unwrap_or(mime)
            .trim()
            .to_ascii_lowercase();
        base == "text/plain"
            || base == "text/csv"
            || base == "text/markdown"
            || base == "text/html"
            || base == "text/css"
            || base == "text/javascript"
            || base == "application/json"
            || base == "application/xml"
            || base == "image/svg+xml"
    }

    /// Sniff the character encoding of a text file by reading its first bytes.
    /// Returns a canonical charset label (e.g. `utf-8`, `gbk`, `gb18030`,
    /// `shift_jis`) suitable for a `Content-Type` `charset` parameter, or
    /// `None` if the bytes are pure ASCII (charset adds no value there) or
    /// detection has no confident guess. Reads at most `max_bytes` of the path.
    async fn guess_charset(path: &str, max_bytes: usize) -> Option<String> {
        use tokio::io::AsyncReadExt as _;
        let mut file = tokio::fs::File::open(path).await.ok()?;
        let mut buf = vec![0u8; max_bytes];
        let n = file.read(&mut buf).await.ok()?;
        let bytes = &buf[..n];
        // Pure ASCII (or empty) needs no charset hint.
        if bytes.iter().all(|b| b.is_ascii()) {
            return None;
        }
        // chardetng is a byte-pattern detector that never reports UTF-16.
        // Excel "Unicode CSV" exports carry a UTF-16LE/BE BOM, so sniff it
        // explicitly before falling back to statistical detection.
        if bytes.starts_with(&[0xFF, 0xFE]) || bytes.starts_with(&[0xFE, 0xFF]) {
            return Some("utf-16".to_string());
        }
        let mut detector = chardetng::EncodingDetector::new();
        // Only the first `max_bytes` were read, so this is NOT the end of the
        // stream. Passing `last = true` would make chardetng treat a truncated
        // multi-byte sequence at the cut point as malformed and permanently
        // rule out UTF-8, misdetecting large CJK files as windows-1252.
        detector.feed(bytes, false);
        let label = detector.guess(None, true).name().to_ascii_lowercase();
        // chardetng may report windows-1252 for ambiguous Latin text; that is
        // still a valid, useful label for browsers. Only suppress empty labels.
        if label.is_empty() { None } else { Some(label) }
    }

    /// High-level three-stage upload: prepare -> PUT (single or multipart) -> complete.
    /// Serial multipart PUTs (no parallelism for v1). Best-effort delete on failure.
    pub async fn upload_session_file(
        &self,
        sid: &str,
        path: &str,
        name_override: Option<&str>,
        mime: Option<&str>,
    ) -> Result<serde_json::Value> {
        let file_name = name_override.map(String::from).unwrap_or_else(|| {
            std::path::Path::new(path)
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("file")
                .to_string()
        });
        let metadata = tokio::fs::metadata(path).await?;
        let size = metadata.len();
        let mut mime = match mime {
            Some(m) => m.to_string(),
            None => Self::guess_mime_from_extension(&file_name)
                .unwrap_or_else(|| "application/octet-stream".to_string()),
        };
        // For textual types, sniff the real encoding and append a charset so
        // browsers preview (inline) Chinese text without mojibake — apply this
        // whether the MIME was inferred or explicitly passed (e.g. --mime
        // text/markdown on a UTF-8/GBK file). The caller may state the type
        // without knowing/caring about the encoding, so we fill the charset
        // gap; a charset already present in an explicit --mime is respected
        // (not duplicated). The charset flows into prepare's content_type and
        // the PUT Content-Type header, so baas/OSS stores it and the
        // presigned/local download returns it.
        if Self::is_text_mime(&mime) && !mime.to_ascii_lowercase().contains("charset=") {
            if let Some(charset) = Self::guess_charset(path, 64 * 1024).await {
                mime = format!("{mime}; charset={charset}");
            }
        }
        let prepared = self
            .prepare_session_file(sid, &file_name, size, &mime)
            .await?;
        let mode = prepared["mode"].as_str().unwrap_or("single");
        let file_id = prepared["file_id"]
            .as_str()
            .context("missing file_id")?
            .to_string();
        match mode {
            "single" => {
                let url = prepared["upload_url"]
                    .as_str()
                    .context("missing upload_url")?
                    .to_string();
                let file = tokio::fs::File::open(path).await?;
                self.put_session_file_bytes(
                    &url,
                    reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(file)),
                    &mime,
                )
                .await?;
            }
            "multipart" => {
                let part_size = prepared["part_size"]
                    .as_u64()
                    .context("missing part_size")? as usize;
                let parts = prepared["parts"].as_array().context("missing parts")?;
                for (i, p) in parts.iter().enumerate() {
                    let url = p["upload_url"]
                        .as_str()
                        .context("missing part url")?
                        .to_string();
                    let mut f = tokio::fs::File::open(path).await?;
                    use tokio::io::{AsyncReadExt as _, AsyncSeekExt as _};
                    f.seek(std::io::SeekFrom::Start((i as u64) * part_size as u64))
                        .await?;
                    let take = f.take(part_size as u64);
                    self.put_session_file_bytes(
                        &url,
                        reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(take)),
                        &mime,
                    )
                    .await?;
                }
            }
            _ => return Err(anyhow!("unknown mode {}", mode)),
        }
        let final_file = match self.complete_session_file(sid, &file_id).await {
            Ok(v) => v,
            Err(e) => {
                let _ = self.delete_session_file(sid, &file_id).await;
                return Err(e);
            }
        };
        Ok(final_file)
    }

    /// List files in the session workspace. GET /sessions/{sid}/files
    pub async fn list_session_files(
        &self,
        sid: &str,
        prefix: Option<&str>,
        status: Option<&str>,
        limit: Option<u32>,
        offset: Option<u32>,
    ) -> Result<serde_json::Value> {
        let mut params: Vec<String> = Vec::new();
        if let Some(p) = prefix {
            params.push(format!("prefix={}", urlencoding::encode(p)));
        }
        if let Some(s) = status {
            params.push(format!("status={}", urlencoding::encode(s)));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }
        if let Some(o) = offset {
            params.push(format!("offset={}", o));
        }
        let mut url = format!(
            "{}/sessions/{}/files",
            self.base_url,
            urlencoding::encode(sid)
        );
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }
        let resp = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("list session files")?;
        Self::ensure_success(resp, "list session files").await
    }

    /// Delete a file or cancel an in-progress upload. DELETE /sessions/{sid}/files/{id}
    pub async fn delete_session_file(&self, sid: &str, file_id: &str) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/files/{}",
            self.base_url,
            urlencoding::encode(sid),
            file_id
        );
        let resp = self
            .add_auth(self.http_client.delete(&url))
            .send()
            .await
            .context("delete session file")?;
        Self::ensure_success(resp, "delete session file").await
    }

    /// Get file metadata. GET /sessions/{sid}/files/{id}
    pub async fn get_session_file(&self, sid: &str, file_id: &str) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/files/{}",
            self.base_url,
            urlencoding::encode(sid),
            file_id
        );
        let resp = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("get session file")?;
        Self::ensure_success(resp, "get session file").await
    }

    /// Download file bytes. GET /sessions/{sid}/files/{id}/content?ttl=...
    /// Follows presigned redirect or streams directly, writes to --out file.
    pub async fn download_session_file(
        &self,
        sid: &str,
        file_id: &str,
        out: Option<&str>,
        ttl: Option<u64>,
    ) -> Result<String> {
        let mut url = format!(
            "{}/sessions/{}/files/{}/content",
            self.base_url,
            urlencoding::encode(sid),
            file_id
        );
        if let Some(t) = ttl {
            url.push_str(&format!("?ttl={}", t));
        }
        let mut resp = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("download session file")?;
        if resp.status().is_redirection() {
            let location = resp.headers().get(reqwest::header::LOCATION)
                .and_then(|value| value.to_str().ok()).context("Download redirect has no Location")?;
            let target = resp.url().join(location)?;
            if !matches!(target.scheme(), "http" | "https") || target.host_str().is_none()
                || !target.username().is_empty() || target.password().is_some() {
                return Err(anyhow!("Invalid download redirect"));
            }
            // Presigned storage URL authenticates itself; carry no BCN context.
            resp = self.http_client.get(target).send().await.map_err(reqwest::Error::without_url).context("download storage content")?;
        }
        if !resp.status().is_success() {
            let s = resp.status();
            let b = resp.text().await.unwrap_or_default();
            return Err(anyhow!("download failed ({}): {}", s, b));
        }
        let out_path = out
            .map(String::from)
            .unwrap_or_else(|| format!("./{}", file_id));
        let mut f = tokio::fs::File::create(&out_path).await?;
        use futures::StreamExt;
        let mut stream = resp.bytes_stream();
        while let Some(chunk) = stream.next().await {
            tokio::io::AsyncWriteExt::write_all(&mut f, &chunk.map_err(reqwest::Error::without_url)?).await?;
        }
        // Tokio buffers writes on a blocking worker. Wait for the final write
        // and propagate its error before reporting that the file is ready.
        tokio::io::AsyncWriteExt::flush(&mut f).await.context("flush downloaded file")?;
        Ok(out_path)
    }

    /// Generate a no-auth share link. POST /sessions/{sid}/files/{id}/share
    pub async fn share_session_file(
        &self,
        sid: &str,
        file_id: &str,
        ttl: Option<u64>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/files/{}/share",
            self.base_url,
            urlencoding::encode(sid),
            file_id
        );
        let mut body = serde_json::Map::new();
        if let Some(t) = ttl {
            // The HTTP `ShareRequest` DTO deserializes `ttl_seconds` (unknown
            // fields are ignored), so the body key must match exactly — sending
            // `ttl` here would be silently dropped and fall back to the service
            // default, ignoring the caller's requested expiry.
            body.insert("ttl_seconds".to_string(), serde_json::json!(t));
        }
        let resp = self
            .add_auth(
                self.http_client
                    .post(&url)
                    .json(&serde_json::Value::Object(body)),
            )
            .send()
            .await
            .context("share session file")?;
        Self::ensure_success(resp, "share session file").await
    }

    /// Query backend capabilities. GET /sessions/{sid}/files/capabilities
    pub async fn session_file_capabilities(&self, sid: &str) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/files/capabilities",
            self.base_url,
            urlencoding::encode(sid)
        );
        let resp = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("session file capabilities")?;
        Self::ensure_success(resp, "session file capabilities").await
    }
}

#[cfg(test)]
mod tests;
