//! User directory plugin contract.
//!
//! This crate defines the infrastructure-facing extension point for resolving
//! stable employee identifiers to display metadata. Business services decide
//! when to use the returned data and how to fall back when the directory is
//! unavailable.

use async_trait::async_trait;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UserDirectoryProfile {
    pub staff_no: String,
    pub nick_name: Option<String>,
}

#[derive(Debug, Error)]
pub enum UserDirectoryError {
    #[error("user directory configuration error: {0}")]
    Config(String),
    #[error("user directory request failed: {0}")]
    Request(String),
    #[error("user directory response parse failed: {0}")]
    Response(String),
}

#[async_trait]
pub trait UserDirectoryPlugin: Send + Sync {
    async fn lookup_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<UserDirectoryProfile>, UserDirectoryError>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn user_directory_plugin_is_object_safe() {
        fn _assert<T: UserDirectoryPlugin>() {}
        fn _assert_dyn(_: Arc<dyn UserDirectoryPlugin>) {}
    }
}
