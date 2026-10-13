/** Version-pinned public distribution links; no GitHub API or user credentials. */
const REPOSITORY = 'https://github.com/inclusionAI/Avernet';
export const BRIDGE_RELEASES_URL = `${REPOSITORY}/releases`;
export interface BridgeRelease {
  version: string;
  url: string;
  installerUrl: string;
  checksumUrl: string;
}

// Maintainers update this version after verifying a new published distribution.
export const VERIFIED_BRIDGE_BETA: BridgeRelease = {
  version: '0.1.4-beta.1',
  url: `${REPOSITORY}/releases/tag/bcs-bridge-v0.1.4-beta.1`,
  installerUrl: `${REPOSITORY}/releases/download/bcs-bridge-v0.1.4-beta.1/bcs-bridge-installer.sh`,
  checksumUrl: `${REPOSITORY}/releases/download/bcs-bridge-v0.1.4-beta.1/sha256.sum`,
};
