/**
 * AIXCoding qiankun metadata runtime resolver.
 *
 * Umi's qiankun plugin serializes master apps into the generated runtime before
 * the Tern platform has provided window.__TERN__. The closed Internal build
 * therefore injects PRE/PROD metadata as static define values, while this runtime
 * modifier chooses the correct pair after Tern has initialized. Open Core has no
 * Internal overlay, these values are not defined, and the configuration remains
 * untouched.
 */

export type QiankunMasterApp = {
  name: string;
  entry: string;
  publicPath?: string;
  [key: string]: unknown;
};

type AixcodingMicroAppProps = {
  publicPath?: string;
};

type QiankunLifeCycleHook = (
  app: QiankunMasterApp,
  globalContext: Record<string, unknown>,
) => Promise<void> | void;

type QiankunRuntimeLifeCycles = NonNullable<
  NonNullable<QiankunRuntimeConfig['master']>['lifeCycles']
>;

export type QiankunRuntimeConfig = {
  master?: {
    apps?: QiankunMasterApp[];
    lifeCycles?: {
      beforeLoad?: QiankunLifeCycleHook | QiankunLifeCycleHook[];
    } & Record<string, unknown>;
    excludeAssetFilter?: (assetUrl: string) => boolean;
  };
};

type AixcodingRuntimeEnv = 'PRE' | 'PROD';

type TernContext = {
  env?: unknown;
  _localDev?: unknown;
};

type TernWindow = {
  __TERN__?: TernContext;
  injectInfo?: {
    env?: unknown;
  };
};

const AIXCODING_MICRO_APP_NAME = 'aixcoding';

const readDefinedString = (value: string | undefined): string =>
  typeof value === 'string' && value.length > 0 ? value : '';

const buildAixcodingApp = (entry: string, publicPath: string): QiankunMasterApp => ({
  name: AIXCODING_MICRO_APP_NAME,
  entry,
  publicPath,
  version: 'latest',
  // Tern 手工域名部署的 entry 受 SSO 保护；qiankun import-html-entry
  // 需要显式携带跨域登录 Cookie，否则拿到的是 302 后的登录 HTML。
  credentials: true,
  env: 'PROD',
  network: 'OFFICE',
  props: {
    publicPath,
  },
});

function setAixcodingRuntimePublicPath(
  app: QiankunMasterApp,
  globalContext: Record<string, unknown>,
): void {
  if (app.name !== AIXCODING_MICRO_APP_NAME) return;

  // Umi's generated MicroApp component constructs the object passed to
  // loadMicroApp with only name/entry/container/props. The configured top-level
  // publicPath is therefore not available in this lifecycle app object. Keep it
  // duplicated in props so the utoopack fallback always receives the child asset
  // origin, even through the generated component path.
  const props = app.props as AixcodingMicroAppProps | undefined;
  // Qiankun's built-in beforeLoad addon has already set this from the entry's
  // asset origin, so it is a final fallback for generated loadMicroApp shapes.
  const injectedPublicPath = globalContext.__INJECTED_PUBLIC_PATH_BY_QIANKUN__;
  const publicPath =
    readDefinedString(app.publicPath) ||
    readDefinedString(props?.publicPath) ||
    readDefinedString(
      typeof injectedPublicPath === 'string' ? injectedPublicPath : undefined,
    );
  if (!publicPath) return;

  // Utoopack resolves runtime chunks from `publicPath`, but its generated code
  // also consults `TURBOPACK_CHUNK_BASE_PATH` before falling back to the runtime
  // resolver. Set both sandbox globals: `publicPath` keeps the documented utoopack
  // runtime contract, while `TURBOPACK_CHUNK_BASE_PATH` reliably wins in the actual
  // generated runtime when a build tool defines that chunk base path variable.
  // Without these per-sandbox values, utoopack resolves the child's dynamic chunks
  // against the master origin, gets the master's HTML fallback, and fails with `<`
  // as JavaScript.
  globalContext.publicPath = publicPath;
  globalContext.TURBOPACK_CHUNK_BASE_PATH = publicPath;
}

function appendAixcodingPublicPathLifecycle(
  lifeCycles?: QiankunRuntimeLifeCycles,
) {
  const existing = lifeCycles?.beforeLoad;
  const beforeLoad = existing === undefined ? [] : Array.isArray(existing) ? existing : [existing];
  if (beforeLoad.includes(setAixcodingRuntimePublicPath)) {
    return lifeCycles ?? {};
  }

  return { ...lifeCycles, beforeLoad: [...beforeLoad, setAixcodingRuntimePublicPath] };
}

function getTernRuntimeEnv(): AixcodingRuntimeEnv | null {
  if (typeof window === 'undefined') return null;

  const ternWindow = window as unknown as TernWindow;
  if (ternWindow.__TERN__?._localDev === true) return null;

  const rawEnv = ternWindow.__TERN__?.env || ternWindow.injectInfo?.env || '';
  const runtimeEnv = typeof rawEnv === 'string' ? rawEnv.toUpperCase() : '';

  return runtimeEnv === 'PRE' || runtimeEnv === 'PROD' ? runtimeEnv : null;
}

/**
 * Returns the deployment-specific AIXCoding app when running under a Tern PRE/PROD
 * environment and when the closed Internal overlay has injected both required URLs.
 */
export function getRuntimeAixcodingMicroApp(): QiankunMasterApp | null {
  const runtimeEnv = getTernRuntimeEnv();
  if (!runtimeEnv) return null;

  if (runtimeEnv === 'PRE') {
    const entry =
      typeof TEAMCLAW_AIXCODING_MICRO_APP_PRE_ENTRY === 'undefined'
        ? ''
        : TEAMCLAW_AIXCODING_MICRO_APP_PRE_ENTRY;
    const publicPath =
      typeof TEAMCLAW_AIXCODING_MICRO_APP_PRE_PUBLIC_PATH === 'undefined'
        ? ''
        : TEAMCLAW_AIXCODING_MICRO_APP_PRE_PUBLIC_PATH;
    return readDefinedString(entry) && readDefinedString(publicPath)
      ? buildAixcodingApp(entry, publicPath)
      : null;
  }

  const entry =
    typeof TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY === 'undefined'
      ? ''
      : TEAMCLAW_AIXCODING_MICRO_APP_PROD_ENTRY;
  const publicPath =
    typeof TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH === 'undefined'
      ? ''
      : TEAMCLAW_AIXCODING_MICRO_APP_PROD_PUBLIC_PATH;
  return readDefinedString(entry) && readDefinedString(publicPath)
    ? buildAixcodingApp(entry, publicPath)
    : null;
}

/**
 * Adds or rewrites the AIXCoding master app after Tern has published its runtime env.
 */
export function applyAixcodingMicroAppRuntimeConfig(config: QiankunRuntimeConfig): QiankunRuntimeConfig {
  const currentApps = config.master?.apps ?? [];
  const localAixcodingApp = currentApps.find((app) => app.name === AIXCODING_MICRO_APP_NAME);
  const aixcodingApp = getRuntimeAixcodingMicroApp() ?? localAixcodingApp;
  if (!aixcodingApp) return config;

  const hasAixcodingApp = currentApps.some((app) => app.name === AIXCODING_MICRO_APP_NAME);
  const apps = hasAixcodingApp
    ? currentApps.map((app) =>
        app.name === AIXCODING_MICRO_APP_NAME ? { ...app, ...aixcodingApp } : app,
      )
    : [...currentApps, aixcodingApp];

  return {
    ...config,
    master: {
      ...config.master,
      apps,
      lifeCycles: appendAixcodingPublicPathLifecycle(config.master?.lifeCycles),
    },
  };
}
