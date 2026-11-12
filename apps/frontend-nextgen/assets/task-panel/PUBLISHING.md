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

The UMD embeds the package version automatically. Hosts check `hostApiVersion`
and required exports, not a compiled-in package version. Select the verified
immutable release URL in system-config after publication.

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

First validate unpublished assets locally against the pre business backend,
following this package's README. This stage needs no npm publication. Then
review/merge the component and host/backend changes into the intended iteration
branch and publish only when separately authorized.

After confirming both published files are reachable, deploy the task manifest
authenticated read endpoint `GET /api/v1/collaboration/tasks/manifest` and configure
the pre environment. There is no OpenAPI mirror. Signed-in users save the complete
configuration through `POST /api/v1/collaboration/tasks/manifest`. The task module
creates the category if absent and replaces the value if present. The server
selects its own environment. Existing generic config APIs retain their behavior.

Example (replace 0.1.1 with an actually published and verified release):

```json
{
  "schema_version": 1,
  "bundles": [{
    "name": "taskPanel",
    "type": "url",
    "url": "https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@0.1.1/dist/index.umd.js"
  }]
}
```

The task interface requires login, not operator permission. Invalid values, category creation failures
and save failures return an error, never success. Both hosts read
`data.bundles` from the manifest and derive `task-panel.css` beside the JS URL.
There is no host asset URL injection or application package-version pin. Updates
need a full page refresh, not an application rebuild or service restart.
Manifest failures have no implicit asset fallback.

Finally verify the actual pre manifest, CDN and both hosts. Local fixture success
is not pre acceptance. Production rollout follows only after that validation and
a separate deployment decision; publishing does not deploy it. Never accept
message/Bot-supplied script URLs or mirror assets into host source/public.

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
- **Bad release:** restore the previous validated immutable JS URL in `task / panel`
  system-config and reload the page. Do not
  hot-swap the global taskPanel version in an active tab. Publish a corrected
  patch; a package owner may deprecate the bad release if necessary.
- **CDN file unavailable:** inspect the exact version's archive and delivery before
  changing manifest configuration. Publication does not itself deploy to pre/prod.

Adding this release mechanism is not authorization to commit, push, tag, publish,
or deploy. Perform each release only after explicit approval.
