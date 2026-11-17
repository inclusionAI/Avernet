use super::{invalid, supported};
use bcs_dist_model::{Digest, InputBody, InputEnvelope, InputId, Sid, Tid};
use bcs_service_api::port::repo::distributed::common::StoreError;
use std::collections::BTreeMap;

type Fields = BTreeMap<String, Vec<u8>>;

/// Fields of one `inbox` stream entry.
pub fn encode_inbox_entry(input: &InputEnvelope) -> Result<Fields, StoreError> {
    supported(input.schema_version)?;
    let body = serde_json::to_vec(&input.body).map_err(|_| invalid("inbox_body_encode"))?;
    let mut fields = Fields::new();
    fields.insert("v".into(), input.schema_version.to_string().into_bytes());
    fields.insert("kind".into(), kind_of(&input.body).as_bytes().to_vec());
    fields.insert("input_id".into(), input.input_id.0.clone().into_bytes());
    fields.insert("tid".into(), input.tid.0.clone().into_bytes());
    fields.insert("digest".into(), input.business_digest.0.clone().into_bytes());
    fields.insert("body".into(), body);
    Ok(fields)
}

/// Rebuilds the envelope of an `inbox` entry of session `sid`.
pub fn decode_inbox_entry(sid: &Sid, fields: &Fields) -> Result<InputEnvelope, StoreError> {
    let text = |name: &str| -> Result<String, StoreError> {
        let bytes = fields.get(name).ok_or_else(|| invalid("inbox_field_missing"))?;
        String::from_utf8(bytes.clone()).map_err(|_| invalid("inbox_field_utf8"))
    };
    let schema_version = text("v")?
        .parse::<u32>()
        .map_err(|_| invalid("inbox_version"))?;
    supported(schema_version)?;
    let body_bytes = fields.get("body").ok_or_else(|| invalid("inbox_field_missing"))?;
    let body: InputBody =
        serde_json::from_slice(body_bytes).map_err(|_| invalid("inbox_body_decode"))?;
    Ok(InputEnvelope {
        schema_version,
        sid: sid.clone(),
        tid: Tid(text("tid")?),
        input_id: InputId(text("input_id")?),
        business_digest: Digest(text("digest")?),
        body,
    })
}

/// Fields of one `wake` stream entry: the session that has input.
pub fn encode_wake_entry(sid: &Sid) -> Fields {
    let mut fields = Fields::new();
    fields.insert("sid".into(), sid.0.clone().into_bytes());
    fields
}

fn kind_of(body: &InputBody) -> &'static str {
    match body {
        InputBody::User { .. } => "user",
        InputBody::HumanReply { .. } => "human_reply",
        InputBody::Cancel { .. } => "cancel",
        InputBody::Agent { .. } => "agent",
    }
}
