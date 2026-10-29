#[path = "../../../build_support/git_metadata.rs"]
mod git_metadata;

fn main() {
    git_metadata::track_changes();
    let hash = git_metadata::commit_hash();
    let date = build_date();
    println!("cargo:rustc-env=GIT_COMMIT_HASH={hash}");
    println!("cargo:rustc-env=BUILD_DATE={date}");
    println!("cargo:rerun-if-env-changed=BCS_CLI_DEFAULT_PRE_URL");
    println!("cargo:rerun-if-env-changed=BCS_CLI_DEFAULT_PROD_URL");
}

fn build_date() -> String {
    chrono::Local::now().format("%Y-%m-%d").to_string()
}
