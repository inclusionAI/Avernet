declare module 'chardet' {
  export function detect(buffer: Uint8Array | Buffer): string | null;
}
