use base64::{
    Engine,
    engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD},
};
use serde_json::{Value, json};

pub(super) fn format_jwt(token: &str, agentpass_resolved: bool) -> Value {
    let segments = token.split('.').collect::<Vec<_>>();
    if segments.len() != 3 || segments.iter().any(|segment| segment.is_empty()) {
        return parse_error(
            "invalid_segment_count",
            agentpass_resolved,
            false,
            None,
        );
    }

    let signature = match decode_segment(segments[2]) {
        Ok(signature) => signature,
        Err(()) => {
            return parse_error(
                "invalid_signature_encoding",
                agentpass_resolved,
                true,
                None,
            );
        }
    };
    let signature_len = Some(signature.len());

    let header_bytes = match decode_segment(segments[0]) {
        Ok(header) => header,
        Err(()) => {
            return parse_error(
                "invalid_header_encoding",
                agentpass_resolved,
                true,
                signature_len,
            );
        }
    };
    let header = match serde_json::from_slice::<Value>(&header_bytes) {
        Ok(header) => header,
        Err(_) => {
            return parse_error(
                "invalid_header_json",
                agentpass_resolved,
                true,
                signature_len,
            );
        }
    };

    let payload_bytes = match decode_segment(segments[1]) {
        Ok(payload) => payload,
        Err(()) => {
            return parse_error(
                "invalid_payload_encoding",
                agentpass_resolved,
                true,
                signature_len,
            );
        }
    };
    let payload = match serde_json::from_slice::<Value>(&payload_bytes) {
        Ok(payload) => payload,
        Err(_) => {
            return parse_error(
                "invalid_payload_json",
                agentpass_resolved,
                true,
                signature_len,
            );
        }
    };

    json!({
        "header": header,
        "payload": payload,
        "signature": {
            "present": true,
            "byte_length": signature.len(),
        },
        "agentpass_resolved": agentpass_resolved,
    })
}

fn decode_segment(segment: &str) -> Result<Vec<u8>, ()> {
    URL_SAFE_NO_PAD
        .decode(segment)
        .or_else(|_| URL_SAFE.decode(segment))
        .map_err(|_| ())
}

fn parse_error(
    category: &'static str,
    agentpass_resolved: bool,
    signature_present: bool,
    signature_len: Option<usize>,
) -> Value {
    json!({
        "header": Value::Null,
        "payload": Value::Null,
        "signature": {
            "present": signature_present,
            "byte_length": signature_len,
        },
        "agentpass_resolved": agentpass_resolved,
        "parse_error": category,
    })
}
