pub mod memory;
pub mod mysql;
mod delivery;

pub use memory::MemoryMessageRepo;
pub use mysql::MySqlMessageStore;

/// Public history never exposes queue-retained capability URLs. Authorized
/// history services may mint fresh links through the existing file service.
fn history_projection(mut message: bcs_domain::PersistedMessage) -> bcs_domain::PersistedMessage {
    if let Some(content) = message.content.as_object_mut() {
        content.remove("channel_sender_identity");
        content.remove("source_im_message_id");
        if let Some(display) = content.remove("queue_display_text") { content.insert("text".into(), display); }
    }
    if let Some(attachments) = message.content.get_mut("attachments").and_then(serde_json::Value::as_array_mut) {
        for attachment in attachments {
            if let Some(object) = attachment.as_object_mut() {
                object.remove("url");
                object.remove("expires_at");
            }
        }
    }
    message
}
