//! Channel-neutral attachment data used by inbound message use cases.

use serde::{Deserialize, Serialize};

/// Attachment categories supported by the BCS application model.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AttachmentType {
    Image,
}

/// A temporary attachment reference carried through the message flow.
///
/// The URL is an ephemeral capability and must not be persisted. Persisted
/// message history should use [`Attachment::stable_metadata`] instead.
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
    fn stable_metadata_excludes_temporary_capability() {
        let attachment = Attachment {
            attachment_id: "att_1".to_string(),
            attachment_type: AttachmentType::Image,
            file_name: "image.png".to_string(),
            mime_type: Some("image/png".to_string()),
            size: Some(4),
            sha256: Some("abcd".to_string()),
            url: "https://download.example.com/image?token=temporary".to_string(),
            expires_at: Some(123),
        };

        let stable = attachment.stable_metadata();
        assert_eq!(stable["attachment_id"], "att_1");
        assert!(stable.get("url").is_none());
        assert!(stable.get("expires_at").is_none());
        assert!(!stable.to_string().contains("token=temporary"));
    }
}
