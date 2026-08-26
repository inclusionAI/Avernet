/**
 * Factory that produces an {@link IApiClient} based on {@link ApiClientFactoryConfig.signMode}.
 *
 *   - `"none"`        → Avernet community default `ApiClient` (conditional signature:
 *                       signs with Ed25519 only when `privateKeyB64` is configured).
 *   - `"ed25519-iam"` → reserved for the enterprise (OCB) implementation. The OCB
 *                       `CorpApiClient` is injected via the OCB plugin, so this factory
 *                       throws here — the community build has no Ed25519+IAM client.
 *
 * This keeps the unified factory in the open-source repo while delegating the
 * enterprise path to the enterprise extension layer.
 */
import { ApiClient } from "../api-client.js";
import type { IApiClient } from "./types.js";
import type { ApiClientFactoryConfig } from "./types.js";

/**
 * Create an API client for the given configuration.
 *
 * @param config Factory configuration (see {@link ApiClientFactoryConfig}).
 * @throws If `config.signMode` is `"ed25519-iam"` (requires enterprise injection).
 */
export function createApiClient(config: ApiClientFactoryConfig): IApiClient {
  const mode = config.signMode ?? "none";

  if (mode === "none") {
    // Community default: conditional Ed25519 signing.
    // The real `ApiClient` constructor accepts `ApiClientConfig | unknown`;
    // `ApiClientFactoryConfig` satisfies it. A type assertion is used because the
    // `get` second-parameter signature (`query` vs the interface's `queryOrConfig`)
    // and the generic default (`any` vs `unknown`) differ slightly, though both are
    // structurally compatible at runtime.
    const client = new ApiClient({
      baseUrl: config.baseUrl,
      timeout: config.timeout,
      maxRetries: config.maxRetries,
    });
    return client as unknown as IApiClient;
  }

  // mode === "ed25519-iam"
  throw new Error(
    "signMode 'ed25519-iam' is not available in the community build. "
    + "The enterprise (OCB) implementation must inject its CorpApiClient "
    + "(Ed25519 + IAM) through the enterprise extension layer.",
  );
}