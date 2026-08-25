use serde_json::Value;
use url::Url;

#[derive(Clone)]
pub struct SessionFileUrlProjector {
    public_base: Url,
}

impl SessionFileUrlProjector {
    pub fn new(public_base: String) -> Result<Self, String> {
        let public_base = Url::parse(&public_base)
            .map_err(|_| "public collaboration base URL is invalid".to_string())?;
        if !matches!(public_base.scheme(), "http" | "https") || public_base.host().is_none() {
            return Err("public collaboration base URL must use HTTP(S)".to_string());
        }
        Ok(Self { public_base })
    }

    fn route_url(&self, segments: &[&str]) -> Url {
        let mut url = self.public_base.clone();
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
                            Value::String(format!("{content_url}?part={part_number}")),
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

impl bcs_service_api::application::v1::SessionFileSharedContentUrlProjector
    for SessionFileUrlProjector
{
    fn shared_content_url(&self, token: &str) -> String {
        SessionFileUrlProjector::shared_content_url(self, token)
    }
}
