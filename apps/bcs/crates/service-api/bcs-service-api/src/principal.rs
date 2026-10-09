// Compatibility shim: the principal type identity now lives in
// `application::principal`, but the old `bcs_service_api::principal::*` path
// remains supported.
pub use crate::application::principal::*;
