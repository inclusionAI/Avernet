export const skillListPageSize = 20

export default function SkillListPagination({ total, page, onChange }: { total: number; page: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / skillListPageSize))
  return <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-5 py-4 text-xs text-gray-500">
    <span>共 {total} 条，第 {page} / {pages} 页</span>
    <div className="flex items-center gap-2">
      <button disabled={page <= 1} onClick={() => onChange(page - 1)} className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">上一页</button>
      <span className="rounded-md bg-blue-600 px-2.5 py-1.5 font-medium text-white">{page}</span>
      <button disabled={page >= pages} onClick={() => onChange(page + 1)} className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40">下一页</button>
    </div>
  </div>
}
