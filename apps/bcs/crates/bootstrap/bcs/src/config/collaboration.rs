use std::path::PathBuf;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollaborationConfig {
    #[serde(default)]
    pub templates: CollaborationTemplatesConfig,
    #[serde(default)]
    pub fixed_loop_limits: bcs_config_api::FixedLoopLimits,
    /// Enable Loop execution and recovery.
    /// Disabled by default; deployments must opt in explicitly.
    #[serde(default)]
    pub loop_execution_enabled: bool,
    /// Admit new Runs whose fixed Loop bodies contain another fixed Loop.
    /// Existing admitted Runs are not governed by this switch.
    #[serde(default)]
    pub nested_loop_execution_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OpenApiV1Config {
    /// Explicitly approved Providers accepting registration by any authenticated
    /// Human. Empty means only the Provider creator/owners may issue tokens.
    #[serde(default)]
    pub registration_self_service_provider_ids: Vec<String>,
    #[serde(default = "default_openapi_v1_public_collaboration_base_url")]
    pub public_collaboration_base_url: String,
    /// Base URL for internal-collaboration endpoints that live under a
    /// different gateway path than the public openapi prefix (e.g. the no-auth
    /// shared-file download at `/api/v1/collaboration/sessions/shared-file/content`).
    /// Defaults to `public_collaboration_base_url` when unset.
    #[serde(default)]
    pub internal_collaboration_base_url: Option<String>,
}

impl Default for OpenApiV1Config {
    fn default() -> Self {
        Self {
            registration_self_service_provider_ids: Vec::new(),
            public_collaboration_base_url: default_openapi_v1_public_collaboration_base_url(),
            internal_collaboration_base_url: None,
        }
    }
}

impl OpenApiV1Config {
    pub fn validated_public_collaboration_base_url(&self) -> Result<String, String> {
        let raw = self.public_collaboration_base_url.trim();
        if raw.is_empty() {
            return Err("openapi_v1.public_collaboration_base_url must not be blank".to_string());
        }
        let mut url = url::Url::parse(raw).map_err(|_| {
            "openapi_v1.public_collaboration_base_url must be an absolute HTTP(S) URL".to_string()
        })?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
            return Err(
                "openapi_v1.public_collaboration_base_url must be an absolute HTTP(S) URL"
                    .to_string(),
            );
        }
        if !url.username().is_empty() || url.password().is_some() {
            return Err(
                "openapi_v1.public_collaboration_base_url must not contain userinfo".to_string(),
            );
        }
        if url.query().is_some() || url.fragment().is_some() {
            return Err(
                "openapi_v1.public_collaboration_base_url must not contain query or fragment"
                    .to_string(),
            );
        }
        let normalized_path = url.path().trim_end_matches('/').to_string();
        url.set_path(&normalized_path);
        Ok(url.to_string().trim_end_matches('/').to_string())
    }

    /// Returns the validated internal-collaboration base URL, falling back to
    /// `public_collaboration_base_url` when `internal_collaboration_base_url`
    /// is not set.
    pub fn validated_internal_collaboration_base_url(&self) -> Result<String, String> {
        let raw = self
            .internal_collaboration_base_url
            .as_deref()
            .map(|v| v.trim())
            .unwrap_or("");
        if raw.is_empty() {
            return self.validated_public_collaboration_base_url();
        }
        let mut url = url::Url::parse(raw).map_err(|_| {
            "openapi_v1.internal_collaboration_base_url must be an absolute HTTP(S) URL"
                .to_string()
        })?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
            return Err(
                "openapi_v1.internal_collaboration_base_url must be an absolute HTTP(S) URL"
                    .to_string(),
            );
        }
        if !url.username().is_empty() || url.password().is_some() {
            return Err(
                "openapi_v1.internal_collaboration_base_url must not contain userinfo".to_string(),
            );
        }
        if url.query().is_some() || url.fragment().is_some() {
            return Err(
                "openapi_v1.internal_collaboration_base_url must not contain query or fragment"
                    .to_string(),
            );
        }
        let normalized_path = url.path().trim_end_matches('/').to_string();
        url.set_path(&normalized_path);
        Ok(url.to_string().trim_end_matches('/').to_string())
    }
}

fn default_openapi_v1_public_collaboration_base_url() -> String {
    "http://127.0.0.1:21000/api/v1/collaboration".to_string()
}

impl Default for CollaborationConfig {
    fn default() -> Self {
        Self {
            templates: CollaborationTemplatesConfig::default(),
            fixed_loop_limits: bcs_config_api::FixedLoopLimits::default(),
            loop_execution_enabled: false,
            nested_loop_execution_enabled: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CollaborationTemplatesConfig {
    #[serde(default)]
    pub storage_type: CollaborationTemplateStorageKind,

    #[serde(default = "default_collaboration_templates_base_dir")]
    pub base_dir: PathBuf,

    #[serde(default = "default_collaboration_templates_default_language")]
    pub default_language: String,
}

impl Default for CollaborationTemplatesConfig {
    fn default() -> Self {
        Self {
            storage_type: CollaborationTemplateStorageKind::default(),
            base_dir: default_collaboration_templates_base_dir(),
            default_language: default_collaboration_templates_default_language(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CollaborationTemplateStorageKind {
    File,
    Mysql,
}

impl Default for CollaborationTemplateStorageKind {
    fn default() -> Self {
        Self::File
    }
}

fn default_collaboration_templates_base_dir() -> PathBuf {
    PathBuf::from("/data/bcs/collaboration-templates")
}

fn default_collaboration_templates_default_language() -> String {
    "zh-CN".to_string()
}
