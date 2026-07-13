//! Attachment DTOs shared by bot WebSocket and HTTP provider transports.

use serde::{Deserialize, Serialize};

/// Attachment categories supported by the public BCS protocol.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AttachmentType {
    Image,
}

/// A temporary attachment reference delivered alongside `chat.send`/`chat.inject`.
///
/// `url` may be a provider-issued short-lived capability URL. It must never
/// contain a DingTalk download code, access token, or a long-lived BCS
/// credential. Metadata unavailable from the provider is omitted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attachment {
    pub attachment_id: String,
    #[serde(rename = "type")]
    pub attachment_type: AttachmentType,
    pub file_name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mime_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<u64>,
}

impl Attachment {
    /// Stable metadata suitable for message history persistence.
    ///
    /// Short-lived URLs and their expiry are intentionally excluded so message
    /// history never persists an upstream temporary capability URL.
    pub fn stable_metadata(&self) -> serde_json::Value {
        serde_json::json!({
            "attachment_id": self.attachment_id,
            "type": self.attachment_type,
            "file_name": self.file_name,
            "mime_type": self.mime_type,
            "size": self.size,
            "sha256": self.sha256,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::{Attachment, AttachmentType};

    #[test]
    fn attachment_wire_shape_and_stable_metadata() {
        let attachment = Attachment {
            attachment_id: "att_1".to_string(),
            attachment_type: AttachmentType::Image,
            file_name: "image.png".to_string(),
            mime_type: Some("image/png".to_string()),
            size: Some(4),
            sha256: Some("abcd".to_string()),
            url: "https://bcs.example.com/attachments?id=att_1&token=short".to_string(),
            expires_at: Some(123),
        };

        let wire = serde_json::to_value(&attachment).expect("serialize attachment");
        assert_eq!(wire["type"], "image");
        assert_eq!(wire["url"], attachment.url);

        let stable = attachment.stable_metadata();
        assert_eq!(stable["attachment_id"], "att_1");
        assert!(stable.get("url").is_none());
        assert!(stable.get("expires_at").is_none());
    }

    #[test]
    fn accepts_temporary_image_url_without_unavailable_metadata() {
        let attachment: Attachment = serde_json::from_value(serde_json::json!({
            "attachment_id": "att_1",
            "type": "image",
            "file_name": "image",
            "url": "https://download.example.com/temporary"
        }))
        .expect("deserialize temporary image attachment");

        assert_eq!(attachment.attachment_type, AttachmentType::Image);
        assert_eq!(attachment.mime_type, None);
        assert_eq!(attachment.size, None);
        assert_eq!(attachment.sha256, None);
        assert_eq!(attachment.expires_at, None);
    }
}
