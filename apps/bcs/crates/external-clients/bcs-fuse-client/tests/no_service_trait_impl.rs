//! External BCSFuse client must stay transport-only.

use std::fs;

#[test]
fn no_service_trait_impl_allowed() {
    let src_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut violations = Vec::new();
    visit(&src_dir, &mut violations);
    assert!(
        violations.is_empty(),
        "external-clients/bcs-fuse-client must not implement service traits:\n{}",
        violations.join("\n")
    );
}

fn visit(dir: &std::path::Path, out: &mut Vec<String>) {
    for entry in fs::read_dir(dir).expect("read dir") {
        let path = entry.expect("entry").path();
        if path.is_dir() {
            visit(&path, out);
            continue;
        }
        if path.extension().and_then(|ext| ext.to_str()) != Some("rs") {
            continue;
        }
        let src = fs::read_to_string(&path).expect("read file");
        for (lineno, line) in src.lines().enumerate() {
            let trimmed = line.trim_start();
            if trimmed.starts_with("impl ")
                && (trimmed.contains("CoreService for ")
                    || trimmed.contains("Service for ")
                    || trimmed.contains("Port for "))
            {
                out.push(format!(
                    "{}:{}: {}",
                    path.display(),
                    lineno + 1,
                    line.trim()
                ));
            }
        }
    }
}
