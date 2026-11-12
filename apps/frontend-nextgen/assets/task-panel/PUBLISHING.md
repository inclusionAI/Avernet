# Publishing the task-panel package

Follow the [BCS panel release model](../../../bcs/assets/panel/PUBLISHING.md).
The package is `@avernet-assets/task-panel`; source lives here, not in a frontend
host. Its dedicated workflow is
[`publish-task-panel.yml`](../../../../.github/workflows/publish-task-panel.yml).

## Configure npm trusted publishing

As a package owner, configure the npm Trusted Publisher:

| Field | Value |
| --- | --- |
| Publisher | GitHub Actions |
| Organization or user | `inclusionAI` |
| Repository | `Avernet` |
| Workflow filename | `publish-task-panel.yml` |
| Environment name | Leave empty |
| Allowed action | `npm publish` |

The filename is case-sensitive; enter only the filename, not the directory.
The workflow uses a GitHub-hosted runner, Node 24, and only `contents: read` /
`id-token: write`. Do not create an npm publish token or add `NPM_TOKEN`.
A package owner must confirm initial package creation and publisher access before
treating the first release as ready. This implementation does not create the npm
package, configure its permissions, or publish anything.

See [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/) for the
registry's current setup requirements.

## Package metadata

Keep these fields aligned with the publishing identity:

```json
{
  "name": "@avernet-assets/task-panel",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/inclusionAI/Avernet.git",
    "directory": "apps/frontend-nextgen/assets/task-panel"
  },
  "publishConfig": {
    "registry": "https://registry.npmjs.org/",
    "access": "public"
  }
}
```

`main`, `browser`, `unpkg` and `jsdelivr` point to `dist/index.umd.js`;
`types` points to `dist/public-api.d.ts`. The public allowlist includes `dist`,
README, this guide, LICENSE and the development static-server script. Inspect
the archive: JS, CSS and declarations are expected; TypeScript implementation
source, credentials, node_modules and runtime state are not. Declarations belong
in the published package, not generated host source directories.

`prepack` runs the complete `verify` suite, including UMD rendering, independent
HTTP delivery and source-boundary checks. Pack and publish must not skip it.

## Prepare a release

From the repository root:

```bash
cd apps/frontend-nextgen/assets/task-panel
npm ci

# Choose patch, minor or major for the compatibility impact. An explicitly
# approved first release may retain the current unpublished version instead.
npm version patch --no-git-tag-version
npm run verify
npm pack --dry-run
```

Review the archive and both version files. Submit the package changes and
`package.json` / `package-lock.json` through the normal review process on the selected release branch: `dev` or
a current `RELYYYYMMDD` iteration branch. The release commit must be pushed to that
remote branch before tagging; merging it into `dev` is not required. Exercise both hosts against the same
candidate asset; a build alone does not prove authentication, routing or file
access.

The UMD embeds the package version automatically. Both hosts also pin the expected
version in their task-specific `assetLoader.ts` / `taskPanelAssetLoader.ts`.
Review and update those pins when changing the release version; otherwise they
correctly reject the new asset. Publishing does not change type-only resolution
or deploy either host.

## Publish from the selected release branch

After review, push the release commit to the chosen `dev` or `RELYYYYMMDD` iteration
branch. Fetch its latest state and use a clean checkout of the exact commit you
intend to release. Branch selection is explicit: CI does not guess the newest
iteration by sorting branch names. A tag fixes the version to that commit; it
does not follow later branch changes. No dev merge is required.

Only exact `REL` + eight-digit date names are accepted, such as `REL20261112`.
Suffixes are forbidden: `REL20261112_task`, `REL20261112-fix` and
`REL20261029_task_3` are not release branches. `dev` remains allowed.

Then run:

```bash
cd apps/frontend-nextgen/assets/task-panel
panel_version="$(node -p "require('./package.json').version")"
cd ../../../..
git tag -a "task-panel-v${panel_version}" -m "Release task panel v${panel_version}"
git push origin "task-panel-v${panel_version}"
```

The **Publish task panel** action checks that the commit is reachable from remote
`origin/dev` or an `origin/RELYYYYMMDD` iteration branch and that the tag exactly matches `task-panel-v<package version>`. It installs locked
dependencies, runs `npm pack --dry-run` and publishes with `npm publish`; both
pack and publish trigger full `prepack` verification.

After the action succeeds, verify the exact release:

```bash
npm view "@avernet-assets/task-panel@${panel_version}" version dist.integrity
```

## Deliver to both hosts

After publishing and verifying both assets are available, configure an exact
npm-backed CDN version, just like BCS panel delivery. The value is a directory,
not a JS filename. Example for a successfully published version 0.1.0:

```bash
TASK_PANEL_ASSET_BASE_URL=https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@0.1.0/dist
```

The host appends `/index.umd.js` and `/task-panel.css`. Check both return the
intended content and work under the page's resource/security policy and target
network before switching configuration. npm success is not proof of CDN or E2E
success. A trusted independent static host may serve the same immutable files.

frontend-nextgen and open-claw accept this build-time variable or
`window.AVERNET_RUNTIME_CONFIG.TASK_PANEL_ASSET_BASE_URL` (runtime takes precedence).
No BCS business manifest change is required. Do not use `latest`, message/Bot
URLs, or copies in frontend source/public directories. Missing configuration
remains an explicit error, not a fallback to another implementation.

With a reachable published release, a local frontend connecting to pre-production
needs only that asset URL, not a local asset server. To test unpublished source,
build here and run `npm run serve`; point the host at the printed local directory,
using a same-origin development proxy if browser policy blocks access. That
server is a development fixture, not production hosting. Python backend runtime
does not build or serve these assets.

## Rollback and troubleshooting

- **Tag/version mismatch:** create the correct tag before pushing; do not move
  published release tags or overwrite npm versions.
- **Release commit not on an allowed remote branch:** push the reviewed release
  commit to the chosen `dev` or `RELYYYYMMDD` iteration branch before tagging. There is
  no requirement to merge an iteration release into `dev`.
- **Publisher/authentication error:** confirm package ownership, publisher setup,
  exact repository and workflow filename, runner and OIDC permission; do not add
  `NPM_TOKEN` as a workaround.
- **Version exists:** increment the version, review and push the release commit to an allowed
  branch, then create a new tag.
- **Bad release:** configure the previous validated immutable directory together
  with matching host version pins/configuration and reload the page. Do not
  hot-swap the global taskPanel version in an active tab. Publish a corrected
  patch; a package owner may deprecate the bad release if necessary.
- **CDN file unavailable:** inspect the exact version's archive and delivery before
  changing host configuration. Publication does not itself deploy to pre/prod.

Adding this release mechanism is not authorization to commit, push, tag, publish,
or deploy. Perform each release only after explicit approval.
