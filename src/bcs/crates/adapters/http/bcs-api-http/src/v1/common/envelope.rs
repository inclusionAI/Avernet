use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct Envelope<T> {
    pub code: u32,
    pub message: String,
    pub data: T,
    pub request_id: String,
}

impl<T> Envelope<T> {
    pub fn success(
        code: u32,
        message: impl Into<String>,
        data: T,
        request_id: impl Into<String>,
    ) -> Self {
        Self {
            code,
            message: message.into(),
            data,
            request_id: request_id.into(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct ErrorData {
    pub error_code: String,
}
