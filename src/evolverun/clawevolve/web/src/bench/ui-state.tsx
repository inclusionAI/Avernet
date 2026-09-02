export function BenchSkeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: lines }).map((_, idx) => (
        <div key={idx} className="h-4 animate-pulse rounded bg-gray-100" style={{ width: `${95 - idx * 8}%` }} />
      ))}
    </div>
  )
}

export function BenchLoadingState({ message = '正在加载数据...' }: { message?: string }) {
  return (
    <div className="space-y-3">
      <div className="text-sm text-gray-500">{message}</div>
      <BenchSkeleton lines={4} />
    </div>
  )
}

export function BenchEmptyState({ message }: { message: string }) {
  return <div className="rounded-md border border-dashed border-gray-200 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">{message}</div>
}

export function BenchErrorState({ message }: { message: string }) {
  return <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{message}</div>
}
