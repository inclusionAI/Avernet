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

impl From<bcs_domain::Attachment> for Attachment {
    fn from(value: bcs_domain::Attachment) -> Self {
        Self {
            attachment_id: value.attachment_id,
            attachment_type: value.attachment_type.into(),
            file_name: value.file_name,
            mime_type: value.mime_type,
            size: value.size,
            sha256: value.sha256,
            url: value.url,
            expires_at: value.expires_at,
        }
    }
}

impl From<Attachment> for bcs_domain::Attachment {
    fn from(value: Attachment) -> Self {
        Self {
            attachment_id: value.attachment_id,
            attachment_type: value.attachment_type.into(),
            file_name: value.file_name,
            mime_type: value.mime_type,
            size: value.size,
            sha256: value.sha256,
            url: value.url,
            expires_at: value.expires_at,
        }
    }
}

impl From<bcs_domain::AttachmentType> for AttachmentType {
    fn from(value: bcs_domain::AttachmentType) -> Self {
        match value {
            bcs_domain::AttachmentType::Image => Self::Image,
        }
    }
}

impl From<AttachmentType> for bcs_domain::AttachmentType {
    fn from(value: AttachmentType) -> Self {
        match value {
            AttachmentType::Image => Self::Image,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Attachment, AttachmentType};

    #[test]
    fn attachment_wire_shape_and_domain_conversion() {
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

        let domain: bcs_domain::Attachment = attachment.into();
        assert_eq!(domain.attachment_id, "att_1");
        assert_eq!(domain.attachment_type, bcs_domain::AttachmentType::Image);
        assert_eq!(domain.url, "https://bcs.example.com/attachments?id=att_1&token=short");
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
