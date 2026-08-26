# Publishing the BCS Panel Package

The `@avernet-assets/bcs-panel` package is published from this directory by
the `publish-bcs-panel.yml` GitHub Actions workflow. A tag named
`bcs-panel-v<version>` starts the workflow. For example, package version
`1.2.1` must be released with tag `bcs-panel-v1.2.1`. The tagged commit must
already be part of the `dev` branch.

Publishing uses npm trusted publishing (OIDC). Do not create an npm publish
token or add `NPM_TOKEN` to the GitHub repository.

## Configure npm OIDC

As an owner of `@avernet-assets/bcs-panel`, open the package settings on
npmjs.com, add a **Trusted Publisher**, and enter these values:

| Field | Value |
| --- | --- |
| Publisher | GitHub Actions |
| Organization or user | `inclusionAI` |
| Repository | `Avernet` |
| Workflow filename | `publish-bcs-panel.yml` |
| Environment name | Leave empty |
| Allowed action | `npm publish` |

The workflow filename is case-sensitive. Enter only the filename, not
`.github/workflows/publish-bcs-panel.yml`.

The workflow runs on a GitHub-hosted runner and grants only `contents: read`
and `id-token: write`. npm exchanges the workflow's short-lived OIDC identity
for publish access and automatically records package provenance for a public
repository and public package.

## Package metadata

Keep these fields in `package.json` when changing the package metadata:

```json
{
  "name": "@avernet-assets/bcs-panel",
  "version": "<version>",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/inclusionAI/Avernet.git",
    "directory": "src/bcs/assets/panel"
  },
  "publishConfig": {
    "registry": "https://registry.npmjs.org/",
    "access": "public"
  },
  "files": [
    "dist/index.umd.js",
    "README.md",
    "PUBLISHING.md",
    "LICENSE"
  ]
}
```

- `name` must be the npm package configured with the trusted publisher.
- `version` must exactly match the version in the release tag.
- `repository.url` must exactly match the public GitHub repository. The
  `repository.directory` field identifies this package inside the monorepo.
- `publishConfig` makes the scoped package public and prevents accidental
  publication to another registry.
- `files` is the public-package allowlist. Add a file only when npm consumers
  need it.

Trusted publishing does not require an auth token, an `.npmrc` file, or a
`provenance` field in `package.json`. Provenance is automatic for this OIDC
workflow.

## Prepare a release

Install the locked dependencies from the package directory:

```bash
cd src/bcs/assets/panel
npm ci
```

Then update the package version. Choose exactly one of `patch`, `minor`, or
`major` according to the release's compatibility impact:

```bash
npm version patch --no-git-tag-version
```

This updates both `package.json` and `package-lock.json`. Verify the updated
version and inspect its package archive:

```bash
npm run verify
npm pack --dry-run
```

Review the dry-run file list and confirm that it contains the intended UMD
bundle, documentation, and license without source files, credentials, or local
build state. Commit the version files with the package changes and open the
normal release pull request:

```bash
panel_version="$(node -p "require('./package.json').version")"
git add package.json package-lock.json
git commit -m "chore(bcs): release panel v${panel_version}"
```

Target the release pull request to `dev`. Do not create the release tag before
the version change is reviewed and merged.

## Publish the merged version

After the release pull request is merged, check out the merged commit, read its
package version, and create the matching annotated tag:

```bash
cd src/bcs/assets/panel
panel_version="$(node -p "require('./package.json').version")"
cd ../../../..
git tag -a "bcs-panel-v${panel_version}" -m "Release BCS panel v${panel_version}"
git push origin "bcs-panel-v${panel_version}"
```

The workflow checks that the tagged commit is part of `dev` and that the tag is
exactly `bcs-panel-v<package version>`. It then installs dependencies with
`npm ci`, runs the package dry run (which triggers the `prepack` verification),
and publishes from `src/bcs/assets/panel`. `npm publish` runs the same `prepack`
verification again before uploading.

Follow the **Publish BCS panel** run in GitHub Actions. After it succeeds,
verify the registry version:

```bash
npm view @avernet-assets/bcs-panel version
```

## Troubleshooting

- **Tag/version mismatch:** delete the incorrect local tag without pushing it,
  or create a new version commit and tag. Never change a published version's
  contents.
- **Tagged commit is not on `dev`:** merge the release pull request into `dev`,
  then create the tag on the merged commit.
- **`ENEEDAUTH`:** confirm that the npm trusted publisher uses organization
  `inclusionAI`, repository `Avernet`, and the exact workflow filename
  `publish-bcs-panel.yml`. Also confirm the job still has `id-token: write`.
- **Version already exists:** npm versions are immutable. Increment the package
  version, merge that change, and create a new matching tag.
- **Bad published release:** publish a corrected patch version. If consumers
  must be warned, a package owner can deprecate the affected version on npm.

## References

- [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/)
- [GitHub Actions OIDC](https://docs.github.com/en/actions/concepts/security/openid-connect)
