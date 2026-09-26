use serde_json::Value;
use url::Url;

#[derive(Clone)]
pub struct SessionFileUrlProjector {
    internal_base: Url,
}

impl SessionFileUrlProjector {
    pub fn new(internal_base: String) -> Result<Self, String> {
        let internal_base =
            validate_base(&internal_base, "internal collaboration base URL")?;
        Ok(Self { internal_base })
    }

    fn route_url(&self, segments: &[&str]) -> Url {
        let mut url = self.internal_base.clone();
        {
            let mut path = url
                .path_segments_mut()
                .expect("validated HTTP(S) URL supports path segments");
            path.pop_if_empty();
            for segment in segments {
                path.push(segment);
            }
        }
        url
    }

    pub fn content_url(&self, session_id: &str, file_id: &str) -> String {
        self.route_url(&["sessions", session_id, "files", file_id, "content"])
            .to_string()
    }

    pub fn shared_content_url(&self, token: &str) -> String {
        let mut url = self.route_url(&["sessions", "shared-file", "content"]);
        url.query_pairs_mut().append_pair("token", token);
        url.to_string()
    }

    pub fn project_upload_target(
        &self,
        mut target: Value,
        proxy_upload: bool,
        session_id: &str,
        file_id: &str,
    ) -> Value {
        if !proxy_upload {
            return target;
        }
        let content_url = self.content_url(session_id, file_id);
        if target.get("mode").and_then(Value::as_str) == Some("multipart") {
            if let Some(parts) = target.get_mut("parts").and_then(Value::as_array_mut) {
                for part in parts {
                    let Some(part_number) = part.get("part_number").and_then(Value::as_u64) else {
                        continue;
                    };
                    if let Some(object) = part.as_object_mut() {
                        object.insert(
                            "upload_url".to_string(),
                            Value::String(format!("{}?part={}", content_url, part_number)),
                        );
                    }
                }
            }
        } else if let Some(object) = target.as_object_mut() {
            object.insert("upload_url".to_string(), Value::String(content_url));
        }
        target
    }
}

fn validate_base(raw: &str, label: &str) -> Result<Url, String> {
    let url = Url::parse(raw).map_err(|_| format!("{} is invalid", label))?;
    if !matches!(url.scheme(), "http" | "https") || url.host().is_none() {
        return Err(format!("{} must use HTTP(S)", label));
    }
    Ok(url)
}

impl bcs_service_api::application::v1::SessionFileInternalContentUrlProjector
    for SessionFileUrlProjector
{
    fn shared_content_url(&self, token: &str) -> String {
        SessionFileUrlProjector::shared_content_url(self, token)
    }
}
