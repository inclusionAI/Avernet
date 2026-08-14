fn main() {
    let hash = git_commit_hash();
    let date = build_date();
    println!("cargo:rustc-env=GIT_COMMIT_HASH={hash}");
    println!("cargo:rustc-env=BUILD_DATE={date}");
    println!("cargo:rerun-if-changed=../../../.git/HEAD");
    println!("cargo:rerun-if-env-changed=BCS_CLI_DEFAULT_PRE_URL");
    println!("cargo:rerun-if-env-changed=BCS_CLI_DEFAULT_PROD_URL");
}

fn git_commit_hash() -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".into())
}

fn build_date() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}
