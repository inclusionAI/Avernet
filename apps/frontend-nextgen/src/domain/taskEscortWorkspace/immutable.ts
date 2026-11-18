/** Copy plain domain data before freezing, so API/caller-owned objects stay untouched. */
export function immutableCopy<T>(value: T): T {
  if (value === null || typeof value !== 'object') return value;
  const copy = Array.isArray(value)
    ? value.map((item) => immutableCopy(item))
    : Object.fromEntries(Object.entries(value).map(([key, item]) => [key, immutableCopy(item)]));
  return Object.freeze(copy) as T;
}
