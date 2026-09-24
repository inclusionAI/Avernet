
    use super::*;

    use clap::{Parser, error::ErrorKind};

    use serial_test::serial;

    use std::io::Write;

    use tempfile::TempDir;



#[path = "cases/safe_set_var.rs"]
mod safe_set_var;
use safe_set_var::*;
#[path = "cases/test_resolve_bcs_url_uses_pre_distribution_default_or_local.rs"]
mod test_resolve_bcs_url_uses_pre_distribution_default_or_local;
#[path = "cases/test_session_patch_parses_args.rs"]
mod test_session_patch_parses_args;
