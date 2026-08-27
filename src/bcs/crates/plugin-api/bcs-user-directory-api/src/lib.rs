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

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct UserDirectoryLookupContext {
    /// Auth-related headers forwarded from the ingress request. Values may
    /// contain credentials; implementations must not log or persist them.
    pub forwarded_headers: Vec<(String, String)>,
}

#[async_trait]
pub trait UserDirectoryPlugin: Send + Sync {
    async fn lookup_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<UserDirectoryProfile>, UserDirectoryError>;

    async fn lookup_department_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<String>, UserDirectoryError>;

    async fn lookup_department_by_staff_no_with_context(
        &self,
        staff_no: &str,
        _context: &UserDirectoryLookupContext,
    ) -> Result<Option<String>, UserDirectoryError> {
        self.lookup_department_by_staff_no(staff_no).await
    }
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
