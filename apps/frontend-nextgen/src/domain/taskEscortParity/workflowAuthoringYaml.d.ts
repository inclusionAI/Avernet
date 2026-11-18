import 'js-yaml';

declare module 'js-yaml' {
  /** The application ambient shim omits the installed package's JSON schema. */
  export const JSON_SCHEMA: unknown;
}
