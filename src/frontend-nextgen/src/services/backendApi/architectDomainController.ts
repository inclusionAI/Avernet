import { backendRequest } from './httpClient';

export interface ArchitectDomainOption {
  label: string;
  value: string;
  code?: string;
  ownerName?: string;
  leaf?: boolean;
  children?: ArchitectDomainOption[];
  raw?: unknown;
}

const SEARCH_TREE_ENDPOINT = '/aixcore/archDomain/tree/searchTree';
const CHILD_KEYS = ['children', 'childList', 'nodes', 'items', 'list', 'tree'];

function unwrapPayload(response: any): any {
  if (response?.success === false) {
    throw new Error(response?.message || response?.errorMessage || '架构域查询失败');
  }
  return (
    response?.data?.result ??
    response?.data?.items ??
    response?.data?.tree ??
    response?.data ??
    response?.result ??
    response?.items ??
    response
  );
}

function pickFirstString(source: any, keys: string[]): string | undefined {
  if (!source || typeof source !== 'object') return undefined;
  for (const key of keys) {
    const value = source[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number') return String(value);
  }
  return undefined;
}

function toTreeNodes(input: any, seen: WeakSet<object>): ArchitectDomainOption[] {
  if (!input) return [];
  if (Array.isArray(input)) return input.flatMap((item) => toTreeNodes(item, seen));
  if (typeof input !== 'object' || seen.has(input)) return [];
  seen.add(input);

  const label = pickFirstString(input, [
    'archDomainName',
    'arch_domain_name',
    'domainName',
    'domain_name',
    'name',
    'title',
    'label',
    'text',
  ]);
  const children = CHILD_KEYS.flatMap((key) => (input[key] ? toTreeNodes(input[key], seen) : []));

  if (!label) {
    return ['data', ...CHILD_KEYS].flatMap((key) => (input[key] ? toTreeNodes(input[key], seen) : []));
  }

  const code = pickFirstString(input, [
    'archDomainCode',
    'arch_domain_code',
    'domainCode',
    'domain_code',
    'code',
    'key',
    'id',
  ]);
  const ownerName = pickFirstString(input.owner || input.ownerInfo || input, [
    'name',
    'userName',
    'user_name',
    'ownerName',
    'owner_name',
  ]);

  return [
    {
      label,
      value: label,
      ...(code ? { code } : {}),
      ...(ownerName ? { ownerName } : {}),
      ...(typeof input.leaf === 'boolean' ? { leaf: input.leaf } : {}),
      ...(children.length ? { children } : {}),
      raw: input,
    },
  ];
}

function dedupeTree(nodes: ArchitectDomainOption[], path = ''): ArchitectDomainOption[] {
  const unique = new Map<string, ArchitectDomainOption>();
  nodes.forEach((node) => {
    const key = `${path}/${node.value}::${node.code || ''}`;
    if (!unique.has(key)) {
      unique.set(key, {
        ...node,
        ...(node.children?.length ? { children: dedupeTree(node.children, key) } : {}),
      });
    }
  });
  return Array.from(unique.values());
}

function filterDeprecatedTree(nodes: ArchitectDomainOption[]): ArchitectDomainOption[] {
  return nodes.flatMap((node) => {
    if (node.label.includes('废弃')) return [];
    const children = node.children ? filterDeprecatedTree(node.children) : [];
    return [{ ...node, ...(children.length ? { children } : { children: undefined }) }];
  });
}

/** 查询架构域树，供“架构域名称”选择字段使用。 */
export async function fetchArchitectDomainOptions(): Promise<ArchitectDomainOption[]> {
  const response = await backendRequest(SEARCH_TREE_ENDPOINT, {
    method: 'POST',
    data: {
      tntInstId: null,
      buNo: null,
      loadExtraInfo: false,
      loadOwnerInfo: true,
    },
    operation: 'search-architect-domain-tree',
    target: 'legacy-agentclaw',
  });
  return filterDeprecatedTree(dedupeTree(toTreeNodes(unwrapPayload(response), new WeakSet<object>())));
}
