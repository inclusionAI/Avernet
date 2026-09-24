//! client implementation.
use super::*;
impl BcsClient {
/// Send a chat message into a session.
    /// `POST /sessions/{sid}/chat`. Caller is resolved from the bearer token.
    pub async fn session_chat(
        &self,
        sid: &str,
        message: &str,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/chat", self.base_url, urlencoding::encode(sid));
        let payload = serde_json::json!({ "message": message });

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to send session chat")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Session chat failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid session chat response")?;
        Ok(result)
    }

/// Fetch message history for a session.
    /// `GET /sessions/{sid}/messages`
    pub async fn session_messages(
        &self,
        sid: &str,
        view_bot_id: Option<&str>,
        limit: Option<u64>,
        before: Option<u64>,
    ) -> Result<serde_json::Value> {
        let mut params: Vec<String> = Vec::new();
        if let Some(v) = view_bot_id {
            params.push(format!("view_bot_id={}", urlencoding::encode(v)));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }
        if let Some(b) = before {
            params.push(format!("before={}", b));
        }

        let mut url = format!("{}/sessions/{}/messages", self.base_url, urlencoding::encode(sid));
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to get session messages")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Get session messages failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid session messages response")?;
        Ok(result)
    }

/// Update session title.
    /// `PATCH /sessions/{sid}`
    pub async fn patch_session(
        &self,
        sid: &str,
        title: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}",
            self.base_url,
            urlencoding::encode(sid)
        );
        let payload = serde_json::json!({ "session_title": title });

        let response = self
            .add_auth(self.http_client.patch(&url).json(&payload))
            .send()
            .await
            .context("Failed to patch session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Patch session failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid patch session response")?;
        Ok(result)
    }

/// Complete a running chat session (driver-only).
    /// `POST /sessions/{sid}/complete`
    pub async fn complete_session(
        &self,
        sid: &str,
        output: Option<&serde_json::Value>,
        error: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/complete",
            self.base_url,
            urlencoding::encode(sid)
        );
        let mut payload = serde_json::Map::new();
        if let Some(output) = output {
            payload.insert("output".to_string(), output.clone());
        }
        if let Some(error) = error {
            payload.insert("error".to_string(), serde_json::json!(error));
        }

        let response = self
            .add_auth(
                self.http_client
                    .post(&url)
                    .json(&serde_json::Value::Object(payload)),
            )
            .send()
            .await
            .context("Failed to complete session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Complete session failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid complete session response")?;
        Ok(result)
    }

/// Add a participant to a session.
    /// `POST /sessions/{sid}/members`
    pub async fn add_session_member(
        &self,
        sid: &str,
        bot_uuid: &str,
        role: Option<&str>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/members",
            self.base_url,
            urlencoding::encode(sid)
        );
        let mut payload = serde_json::json!({ "bot_uuid": bot_uuid });
        if let Some(role) = role {
            payload["role"] = serde_json::json!(role);
        }

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to add session member")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Add session member failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid add session member response")?;
        Ok(result)
    }

/// Remove a participant from a session.
    /// `DELETE /sessions/{sid}/members/{bot_uuid}`
    pub async fn remove_session_member(
        &self,
        sid: &str,
        bot_uuid: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/members/{}",
            self.base_url,
            urlencoding::encode(sid),
            urlencoding::encode(bot_uuid)
        );

        let response = self
            .add_auth(self.http_client.delete(&url))
            .send()
            .await
            .context("Failed to remove session member")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Remove session member failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid remove session member response")?;
        Ok(result)
    }

/// Update a participant's mode in a session.
    /// `PATCH /sessions/{sid}/members/{bot_uuid}`
    pub async fn set_session_member_mode(
        &self,
        sid: &str,
        bot_uuid: &str,
        mode: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/members/{}",
            self.base_url,
            urlencoding::encode(sid),
            urlencoding::encode(bot_uuid)
        );
        let payload = serde_json::json!({ "mode": mode });

        let response = self
            .add_auth(self.http_client.patch(&url).json(&payload))
            .send()
            .await
            .context("Failed to set session member mode")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Set session member mode failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid set member mode response")?;
        Ok(result)
    }

/// Create an invite link for a session.
    /// `POST /sessions/{sid}/invite-link`
    pub async fn create_session_invite_link(
        &self,
        sid: &str,
        ttl_seconds: Option<u64>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/sessions/{}/invite-link",
            self.base_url,
            urlencoding::encode(sid)
        );
        let payload = if let Some(ttl) = ttl_seconds {
            serde_json::json!({ "ttl_seconds": ttl })
        } else {
            serde_json::json!({})
        };

        let response = self
            .add_auth(self.http_client.post(&url).json(&payload))
            .send()
            .await
            .context("Failed to create session invite link")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "Create session invite link failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid create invite link response")?;
        Ok(result)
    }

/// Create a channel binding.
    /// `POST /channels/bindings`
    pub async fn create_channel_binding(
        &self,
        payload: &serde_json::Value,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/channels/bindings", self.base_url);
        let response = self
            .add_auth(self.http_client.post(&url).json(payload))
            .send()
            .await
            .context("Failed to create channel binding")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Create channel binding failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid create channel binding response")?;
        Ok(result)
    }

/// List channel bindings.
    /// `GET /channels/bindings`
    pub async fn list_channel_bindings(&self) -> Result<serde_json::Value> {
        let url = format!("{}/channels/bindings", self.base_url);
        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to list channel bindings")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("List channel bindings failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid list channel bindings response")?;
        Ok(result)
    }

/// List channel conversation mappings for a BCS session.
    /// `GET /channels/conversations/by-session`
    pub async fn list_channel_conversations_by_session(
        &self,
        bcs_session_id: &str,
        channel_type: &str,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/channels/conversations/by-session", self.base_url);
        let response = self
            .add_auth(self.http_client.get(&url).query(&[
                ("bcs_session_id", bcs_session_id),
                ("channel_type", channel_type),
            ]))
            .send()
            .await
            .context("Failed to list channel conversations by session")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!(
                "List channel conversations by session failed ({}): {}",
                status,
                body
            ));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid channel conversation list response")?;
        Ok(result)
    }

/// Delete a channel binding.
    /// `DELETE /channels/bindings/{id}`
    pub async fn delete_channel_binding(&self, id: &str) -> Result<serde_json::Value> {
        let url = format!(
            "{}/channels/bindings/{}",
            self.base_url,
            urlencoding::encode(id)
        );
        let response = self
            .add_auth(self.http_client.delete(&url))
            .send()
            .await
            .context("Failed to delete channel binding")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Delete channel binding failed ({}): {}", status, body));
        }

        let result: serde_json::Value = response
            .json()
            .await
            .context("Invalid delete channel binding response")?;
        Ok(result)
    }

/// Kick off (or reactivate) a service_invocation session under a group.
    /// `POST /services/{group_id}/sessions`
    ///
    /// CLI callers normally send a bot token. `X-BCS-Service-Key` is still
    /// supported by the lower-level client for non-CLI external callers. The
    /// server returns 202 Accepted on success; any 2xx response carries the session JSON
    /// (`service_session_to_json` in `routes/services.rs`).
    pub async fn service_invoke(
        &self,
        group_id: &str,
        input: Option<&serde_json::Value>,
        session_id: Option<&str>,
        caller_id: Option<&str>,
        session_title: Option<&str>,
        meta: Option<&serde_json::Value>,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/services/{}/sessions",
            self.base_url,
            urlencoding::encode(group_id)
        );
        let mut payload = serde_json::Map::new();
        if let Some(sid) = session_id {
            payload.insert("session_id".to_string(), serde_json::json!(sid));
        }
        if let Some(cid) = caller_id {
            payload.insert("caller_id".to_string(), serde_json::json!(cid));
        }
        if let Some(input) = input {
            payload.insert("input".to_string(), input.clone());
        }
        if let Some(title) = session_title {
            payload.insert("session_title".to_string(), serde_json::json!(title));
        }
        if let Some(meta) = meta {
            payload.insert("meta".to_string(), meta.clone());
        }

        let response = self
            .add_auth(
                self.http_client
                    .post(&url)
                    .json(&serde_json::Value::Object(payload)),
            )
            .send()
            .await
            .context("Failed to send service invocation")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Service invoke failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid service invoke response")?;
        Ok(result)
    }

/// Poll a service_invocation session once.
    /// `GET /services/{group_id}/sessions/{session_id}`
    pub async fn service_session_status(
        &self,
        group_id: &str,
        session_id: &str,
    ) -> Result<serde_json::Value> {
        let url = format!(
            "{}/services/{}/sessions/{}",
            self.base_url,
            urlencoding::encode(group_id),
            urlencoding::encode(session_id)
        );

        let response = self
            .add_auth(self.http_client.get(&url))
            .send()
            .await
            .context("Failed to fetch service session status")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(anyhow!("Service session status failed ({}): {}", status, body));
        }

        let result: serde_json::Value =
            response.json().await.context("Invalid service session response")?;
        Ok(result)
    }

/// Prepare a file upload. POST /sessions/{sid}/files
    pub async fn prepare_session_file(&self, sid: &str, file_name: &str, size: u64, mime: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/files", self.base_url, urlencoding::encode(sid));
        let body = serde_json::json!({ "file_name": file_name, "size": size, "mime_type": mime });
        let resp = self.add_auth(self.http_client.post(&url).json(&body)).send().await
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
        let bcs_host = reqwest::Url::parse(&self.base_url).ok().and_then(|u| u.host_str().map(String::from));
        let target_host = reqwest::Url::parse(upload_url).ok().and_then(|u| u.host_str().map(String::from));
        let cross_host = match (bcs_host.as_deref(), target_host.as_deref()) {
            (Some(a), Some(b)) => a != b,
            _ => false,
        };
        let mut req = self.http_client
            .put(upload_url)
            .header(reqwest::header::CONTENT_TYPE, content_type)
            .body(bytes);
        if !cross_host {
            req = self.add_auth(req);
        }
        let resp = req.send().await.context("put session file bytes")?;
        Self::ensure_success_status(resp, "put session file bytes").await
    }

/// Complete a file upload. POST /sessions/{sid}/files/{id}/complete
    pub async fn complete_session_file(&self, sid: &str, file_id: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/files/{}/complete", self.base_url, urlencoding::encode(sid), file_id);
        let resp = self.add_auth(self.http_client.post(&url).json(&serde_json::json!({}))).send().await
            .context("complete session file")?;
        Self::ensure_success(resp, "complete session file").await
    }

/// Infer a MIME type from a file name's extension. Covers common upload
    /// types (text, JSON/HTML/XML, PDF, images, audio/video, archives); unknown
    /// or missing extensions return `None` so callers fall back to the generic
    /// `application/octet-stream`.
    pub(super) fn guess_mime_from_extension(file_name: &str) -> Option<String> {
        let ext = std::path::Path::new(file_name)
            .extension()?
            .to_str()?
            .to_ascii_lowercase();
        Some(match ext.as_str() {
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
        }.to_string())
    }

/// Whether a MIME type is textual and therefore eligible for a `charset`
    /// parameter. Binary types (images other than SVG, archives, audio/video)
    /// never get a charset — appending one would mislabel the octet payload.
    pub(super) fn is_text_mime(mime: &str) -> bool {
        // Normalize to the type before any `;` parameter for the check.
        let base = mime.split(';').next().unwrap_or(mime).trim().to_ascii_lowercase();
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
    pub(super) async fn guess_charset(path: &str, max_bytes: usize) -> Option<String> {
        use tokio::io::{AsyncReadExt as _};
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
        &self, sid: &str, path: &str, name_override: Option<&str>, mime: Option<&str>,
    ) -> Result<serde_json::Value> {
        let file_name = name_override.map(String::from)
            .unwrap_or_else(|| std::path::Path::new(path).file_name().and_then(|n| n.to_str()).unwrap_or("file").to_string());
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
        let prepared = self.prepare_session_file(sid, &file_name, size, &mime).await?;
        let mode = prepared["mode"].as_str().unwrap_or("single");
        let file_id = prepared["file_id"].as_str().context("missing file_id")?.to_string();
        match mode {
            "single" => {
                let url = prepared["upload_url"].as_str().context("missing upload_url")?.to_string();
                let file = tokio::fs::File::open(path).await?;
                self.put_session_file_bytes(&url, reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(file)), &mime).await?;
            }
            "multipart" => {
                let part_size = prepared["part_size"].as_u64().context("missing part_size")? as usize;
                let parts = prepared["parts"].as_array().context("missing parts")?;
                for (i, p) in parts.iter().enumerate() {
                    let url = p["upload_url"].as_str().context("missing part url")?.to_string();
                    let mut f = tokio::fs::File::open(path).await?;
                    use tokio::io::{AsyncReadExt as _, AsyncSeekExt as _};
                    f.seek(std::io::SeekFrom::Start((i as u64) * part_size as u64)).await?;
                    let take = f.take(part_size as u64);
                    self.put_session_file_bytes(&url, reqwest::Body::wrap_stream(tokio_util::io::ReaderStream::new(take)), &mime).await?;
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
        &self, sid: &str, prefix: Option<&str>, status: Option<&str>, limit: Option<u32>, offset: Option<u32>,
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
        let mut url = format!("{}/sessions/{}/files", self.base_url, urlencoding::encode(sid));
        if !params.is_empty() {
            url.push('?');
            url.push_str(&params.join("&"));
        }
        let resp = self.add_auth(self.http_client.get(&url)).send().await
            .context("list session files")?;
        Self::ensure_success(resp, "list session files").await
    }

/// Delete a file or cancel an in-progress upload. DELETE /sessions/{sid}/files/{id}
    pub async fn delete_session_file(&self, sid: &str, file_id: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/files/{}", self.base_url, urlencoding::encode(sid), file_id);
        let resp = self.add_auth(self.http_client.delete(&url)).send().await
            .context("delete session file")?;
        Self::ensure_success(resp, "delete session file").await
    }

/// Get file metadata. GET /sessions/{sid}/files/{id}
    pub async fn get_session_file(&self, sid: &str, file_id: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/files/{}", self.base_url, urlencoding::encode(sid), file_id);
        let resp = self.add_auth(self.http_client.get(&url)).send().await
            .context("get session file")?;
        Self::ensure_success(resp, "get session file").await
    }

/// Download file bytes. GET /sessions/{sid}/files/{id}/content?ttl=...
    /// Follows presigned redirect or streams directly, writes to --out file.
    pub async fn download_session_file(
        &self, sid: &str, file_id: &str, out: Option<&str>, ttl: Option<u64>,
    ) -> Result<String> {
        let mut url = format!("{}/sessions/{}/files/{}/content", self.base_url, urlencoding::encode(sid), file_id);
        if let Some(t) = ttl { url.push_str(&format!("?ttl={}", t)); }
        let resp = self.add_auth(self.http_client.get(&url)).send().await
            .context("download session file")?;
        if !resp.status().is_success() {
            let s = resp.status(); let b = resp.text().await.unwrap_or_default();
            return Err(anyhow!("download failed ({}): {}", s, b));
        }
        let out_path = out.map(String::from).unwrap_or_else(|| format!("./{}", file_id));
        let mut f = tokio::fs::File::create(&out_path).await?;
        use futures::StreamExt;
        let mut stream = resp.bytes_stream();
        while let Some(chunk) = stream.next().await {
            tokio::io::AsyncWriteExt::write_all(&mut f, &chunk?).await?;
        }
        Ok(out_path)
    }

/// Generate a no-auth share link. POST /sessions/{sid}/files/{id}/share
    pub async fn share_session_file(
        &self, sid: &str, file_id: &str, ttl: Option<u64>,
    ) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/files/{}/share", self.base_url, urlencoding::encode(sid), file_id);
        let mut body = serde_json::Map::new();
        if let Some(t) = ttl {
            // The HTTP `ShareRequest` DTO deserializes `ttl_seconds` (unknown
            // fields are ignored), so the body key must match exactly — sending
            // `ttl` here would be silently dropped and fall back to the service
            // default, ignoring the caller's requested expiry.
            body.insert("ttl_seconds".to_string(), serde_json::json!(t));
        }
        let resp = self.add_auth(self.http_client.post(&url).json(&serde_json::Value::Object(body))).send().await
            .context("share session file")?;
        Self::ensure_success(resp, "share session file").await
    }

/// Query backend capabilities. GET /sessions/{sid}/files/capabilities
    pub async fn session_file_capabilities(&self, sid: &str) -> Result<serde_json::Value> {
        let url = format!("{}/sessions/{}/files/capabilities", self.base_url, urlencoding::encode(sid));
        let resp = self.add_auth(self.http_client.get(&url)).send().await
            .context("session file capabilities")?;
        Self::ensure_success(resp, "session file capabilities").await
    }

pub fn admitted_outcome(submit: &ChatRunSubmitResponse) -> ChatRunOutcome {
        let state = submit.status.as_deref().unwrap_or("pending");
        ChatRunOutcome {
            delivery: submit.delivery.clone(), delivered: matches!(state, "running" | "completed"),
            submitted: true, run_id: Some(submit.run_id.clone()), session_id: Some(submit.session_id.clone()),
            bot_uuid: Some(submit.bot_uuid.clone()), state: state.into(), response_content: None,
            error_message: None, content_truncated: false,
        }
    }

}

impl ChatRunOutcome {

pub fn admission_succeeded(&self) -> bool {
        self.submitted && matches!(self.state.as_str(), "pending" | "submitted" | "running" | "completed")
            && self.delivery.as_ref().is_none_or(|d| !matches!(d.status.as_str(), "rejected_capacity" | "failed" | "expired" | "cancelled"))
    }
}
