use std::fs;
use std::path::{Path, PathBuf};

fn workspace_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("bcs crate should live under crates/bootstrap/bcs")
        .to_path_buf()
}

#[test]
fn bcs_client_crate_is_retired_from_workspace() {
    let root = workspace_root();
    let cargo_toml =
        fs::read_to_string(root.join("Cargo.toml")).expect("read workspace Cargo.toml");

    assert!(
        !cargo_toml.contains("\"crates/bcs-client\""),
        "bcs-client must not remain a workspace member"
    );
    assert!(
        !cargo_toml.contains("bcs-client"),
        "bcs-client must not remain a workspace dependency"
    );
    assert!(
        !root.join("crates/bcs-client").exists(),
        "bcs-client crate directory should be deleted after DTOs move to bcs-protocol"
    );
}

#[test]
fn server_and_http_adapter_do_not_reexport_bcs_client() {
    let root = workspace_root();
    let bcs_lib = fs::read_to_string(root.join("crates/bootstrap/bcs/src/lib.rs"))
        .expect("read bootstrap lib");
    let bcs_http_cargo = fs::read_to_string(root.join("crates/adapters/http/bcs-http/Cargo.toml"))
        .expect("read bcs-http Cargo.toml");

    assert!(
        !bcs_lib.contains("bcs_client"),
        "bootstrap crate must not re-export bcs_client symbols"
    );
    assert!(
        !bcs_http_cargo.contains("bcs-client"),
        "HTTP delivery adapter must not depend on the retired client SDK crate"
    );
    assert!(
        !root
            .join("crates/adapters/http/bcs-http/src/runtime_client.rs")
            .exists(),
        "server-side HTTP adapter should not host a client SDK shim"
    );
}
