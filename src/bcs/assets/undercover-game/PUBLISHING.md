# Publishing the Undercover Game Panel

The `@avernet-assets/undercover-game-panel` package is published by
`publish-undercover-game-panel.yml`. Tag the merged `dev` commit as
`undercover-game-panel-v<package-version>`; the workflow rejects tag/package
version mismatches, verifies the UMD named export, and publishes through npm
OIDC without a long-lived token.

When changing the package version, update the production manifest URL in
`src/bcs/configs/bcs-config-prod.toml` in the same change. The manifest and
package must reference the same version: publishing only the asset or only the
manifest would leave callers unable to resolve `undercoverGame.UndercoverGamePanel`.
