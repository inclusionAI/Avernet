use std::fs;

#[test]
fn lib_rs_contains_only_reexports() {
    let lib = fs::read_to_string(format!("{}/src/lib.rs", env!("CARGO_MANIFEST_DIR")))
        .expect("read lib.rs");
    for (lineno, line) in lib.lines().enumerate() {
        let trimmed = line.trim_start();
        assert!(
            !(trimmed.starts_with("pub struct ")
                || trimmed.starts_with("struct ")
                || trimmed.starts_with("impl ")),
            "lib.rs must only declare modules/re-exports; line {}: {}",
            lineno + 1,
            line
        );
    }
}
