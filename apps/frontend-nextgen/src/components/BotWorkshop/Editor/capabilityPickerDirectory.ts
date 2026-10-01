type DirectoryEntry =
  | { kind: 'file'; name: string; getFile: () => Promise<File> }
  | { kind: 'directory'; name: string; values: () => AsyncIterable<DirectoryEntry> };

export type DirectoryHandle = DirectoryEntry & { kind: 'directory' };

export async function readDirectoryFiles(handle: DirectoryHandle, prefix = ''): Promise<File[]> {
  const files: File[] = [];
  for await (const entry of handle.values()) {
    if (entry.kind === 'file') {
      const file = await entry.getFile();
      Object.defineProperty(file, 'webkitRelativePath', {
        configurable: true,
        value: prefix ? `${prefix}/${entry.name}` : entry.name,
      });
      files.push(file);
    } else {
      const nextPrefix = prefix ? `${prefix}/${entry.name}` : entry.name;
      files.push(...(await readDirectoryFiles(entry, nextPrefix)));
    }
  }
  return files;
}
