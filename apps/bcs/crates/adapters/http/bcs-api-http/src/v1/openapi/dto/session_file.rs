use bcs_service_api::application::v1::SessionFileStatus;
use serde::Deserialize;

fn default_list_limit() -> u32 {
    100
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrepareSessionFileRequest {
    pub file_name: String,
    pub size: u64,
    pub mime_type: String,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListSessionFilesQuery {
    #[serde(default)]
    pub prefix: Option<String>,
    #[serde(default)]
    pub status: Option<SessionFileStatus>,
    #[serde(default = "default_list_limit")]
    pub limit: u32,
    #[serde(default)]
    pub offset: u32,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ShareSessionFileRequest {
    #[serde(default)]
    pub ttl_seconds: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UploadSessionFileQuery {
    #[serde(default)]
    pub part: Option<u16>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProtectedFileContentQuery {
    #[serde(default)]
    pub show: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedFileContentQuery {
    pub token: String,
    #[serde(default)]
    pub show: bool,
}
