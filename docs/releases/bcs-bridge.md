# Releasing the BCS bridge macOS preview

This distribution publishes only `bcs-bridge`, not `bcs-cli`, the BCS server or
other workspace tools. `dist-workspace.toml` allow-lists the branded dist package
`bcs-bridge`; its Cargo package remains `bcs-bridge-cli`. `cli/dist.toml` supplies
the public repository URL, archive license and quickstart. Version remains the
BCS workspace version so the shared CLI's `--version` matches the archive.

## Release pipeline

`publish-bcs-bridge.yml` owns monorepo gates and GitHub channel publication;
cargo-dist 0.33.0 owns planning, native archives and shell-installer generation.
The custom workflow avoids changing other products' publishing workflows.

- PRs and manual dispatch validate and build, but never create a Release.
- Only `bcs-bridge-v<VERSION>` tags publish. The tag must exactly match the
  workspace version. Stable releases must point to a commit merged into `dev`;
  beta/rc releases may also come from `codex/bcs-bridge-release`.
- Native `macos-15` arm64 and `macos-15-intel` runners test the four bridge
  crates, build optimized binaries, verify architecture/version, check that
  dynamic libraries resolve only to macOS system locations and smoke-test the
  installer plus daemon start/status/ping/stop. The deployment target is 13.0.
- Archives contain the executable, Apache license and QUICKSTART.md. Every
  archive has a SHA-256 file. Target-specific dist manifests carry actual hashes
  into the global installer build; they are CI scratch artifacts, not public
  Release attachments.
- Installer preparation requires embedded hashes for both archives. It adds
  a `shasum` fallback for standard macOS hosts so cargo-dist cannot silently
  skip SHA-256 verification when `sha256sum` is absent. `sha256.sum` is computed
  after this change. Smoke tests also reject a corrupt download and verify the
  previously installed binary survives it.
- After all gates pass, create or recover a draft Release, upload the validated
  assets, then publish beta/rc as a prerelease and stable versions as ordinary
  Releases, always with `latest=false` because Latest is repository-wide. Published Releases
  cannot be overwritten by this workflow. There are no npm, Homebrew or crates.io
  publishing steps. The preview binaries are not Developer ID notarized.

`Cargo.lock` is checked for changes after planning and builds; test commands use
`--locked`. Rust is pinned to 1.93.0 in CI. The downloaded dist tool is version
pinned and checksum-verified. Configuration, local databases and credentials are
never included in the archive's explicit file list.

## Maintainer validation

Use Python 3.12+ for the release helpers. Install the pinned dist tool into a
temporary directory, or use an existing dist 0.33.0:

```sh
DIST_TOOL_DIR=/tmp/bridge-release-tools bash devops/release/install_dist.sh
python3 -m unittest discover -s devops/release/tests -v
dist plan --tag bcs-bridge-v0.1.4-beta.1 --output-format json > plan-dist-manifest.json
python3 devops/release/bcs_bridge_release.py plan --manifest plan-dist-manifest.json
```

For a local native build, replace the example version/target with the configured
version and your CPU. Run from the repository root:

```sh
dist build --artifacts local --target aarch64-apple-darwin --tag bcs-bridge-v0.1.4-beta.1 --output-format json > local-dist-manifest.json
python3 devops/release/bcs_bridge_release.py native --targets aarch64-apple-darwin
cp local-dist-manifest.json target/distrib/aarch64-apple-darwin-dist-manifest.json
dist build --artifacts global --tag bcs-bridge-v0.1.4-beta.1 --output-format json > global-dist-manifest.json
python3 devops/release/bcs_bridge_release.py prepare --targets aarch64-apple-darwin
python3 devops/release/smoke_bcs_bridge.py --directory target/distrib
```

The local installer smoke uses loopback downloads, a temporary installation
directory and PATH without Rust/Homebrew. It does not change the user's HOME,
shell profiles or QwenWork settings. Add `--lifecycle` only on an otherwise unused
CI runner; it refuses to operate when a bridge is already running. A single-CPU
local build does not certify the other archive; CI tests both natively.

## Version channels and publication

Validate the release code through a PR or manual run. An authorized maintainer
creates the matching tag on `dev`, or on `codex/bcs-bridge-release` for beta/rc
verification before merge. For the current workspace version:

```sh
git tag -a bcs-bridge-v0.1.4-beta.1 -m 'BCS bridge 0.1.4-beta.1 macOS preview'
git push origin bcs-bridge-v0.1.4-beta.1
```

This triggers a public beta Release. Do not reuse a published tag. A future
release updates the workspace version and lockfile as needed before tagging;
do not use dist's `--force-tag` to hide a mismatch. Keeping the current shared
version avoids displaying a different library version in the installed CLI.

Users select a versioned Release and download `bcs-bridge-installer.sh`. An
installer always targets its own release, enabling upgrade/rollback by running
another version's installer. Never advertise the repository-wide latest Release
URL for bridge installation. See the bundled
[QUICKSTART](../../apps/bcs/crates/tools/bcs-bridge/cli/QUICKSTART.md) for QwenWork
setup and the explicit IM AskUserQuestion/subagent compatibility limitations.

Versions use `X.Y.Z-beta.N`, `X.Y.Z-rc.N`, or `X.Y.Z`. For example, publish
`0.1.4-beta.1`, then `0.1.4-beta.2`, then `0.1.4`. The workflow infers the
GitHub prerelease flag from the version; changing the title alone does not
change the channel. Update `[workspace.package].version` and the workspace
packages in `Cargo.lock` together. This changes the shared Rust package versions,
but the release artifact allow-list still publishes only `bcs-bridge`.
