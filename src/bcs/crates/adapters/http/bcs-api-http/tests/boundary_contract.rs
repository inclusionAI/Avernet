use std::fs;
use std::path::{Path, PathBuf};

fn rust_files_under(directory: &Path, files: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(directory).expect("read source directory") {
        let path = entry.expect("read source entry").path();
        if path.is_dir() {
            rust_files_under(&path, files);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            files.push(path);
        }
    }
}

#[test]
fn manifest_does_not_depend_on_legacy_or_concrete_bcs_crates() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let manifest =
        fs::read_to_string(manifest_dir.join("Cargo.toml")).expect("read adapter manifest");

    for forbidden in [
        "bcs-protocol",
        "bcs-http",
        "bcs-jwt",
        "bcs-group",
        "bcs-session",
        "bcs-friend",
        "bcs-bot",
        "bcs-relation",
    ] {
        let dependency_prefix = format!("{forbidden} ");
        assert!(
            !manifest
                .lines()
                .any(|line| line.trim_start().starts_with(&dependency_prefix)),
            "versioned HTTP adapter must not depend on {forbidden}"
        );
    }
}

#[test]
fn production_bootstrap_does_not_mount_the_versioned_http_adapter() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let bootstrap_manifest =
        match fs::read_to_string(manifest_dir.join("../../../bootstrap/bcs/Cargo.toml")) {
            Ok(source) => source,
            Err(_) => panic!("read production bootstrap manifest"),
        };

    assert!(
        !bootstrap_manifest
            .lines()
            .any(|line| { line.trim_start().starts_with("bcs-api-http ") }),
        "production bootstrap must not mount bcs-api-http in this preparatory change"
    );
}

#[test]
fn production_sources_use_only_the_application_service_api_boundary() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut files = Vec::new();
    rust_files_under(&manifest_dir.join("src"), &mut files);

    let offenders = files
        .into_iter()
        .filter_map(|path| {
            let source = fs::read_to_string(&path).expect("read source file");
            let violates_boundary = source.contains("bcs_protocol")
                || source.contains("bcs_service_api::core")
                || source.contains("bcs_service_api::port")
                || source.lines().any(|line| {
                    line.trim_start().starts_with("use bcs_service_api::")
                        && !line.contains("application::")
                });
            violates_boundary.then_some(path)
        })
        .collect::<Vec<_>>();

    assert!(
        offenders.is_empty(),
        "HTTP adapter must call only bcs_service_api::application: {offenders:?}"
    );
}
