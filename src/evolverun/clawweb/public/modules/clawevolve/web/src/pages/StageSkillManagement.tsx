import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvolveStageMode, type EvolveStageSkill } from '../api/client'

const modeName: Record<EvolveStageMode, string> = {
  preprocess: '前置处理',
  postprocess: '后置处理',
  replace: '整体替换',
}

const statusName: Record<EvolveStageSkill['status'], string> = {
  validated: '静态校验通过',
  testing: '测试中',
  test_passed: '集成测试通过',
  test_failed: '集成测试失败',
  registered: '已注册',
  deleted: '已删除',
}

export default function StageSkillManagement() {
  const navigate = useNavigate()
  const [items, setItems] = useState<EvolveStageSkill[]>([])
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const result = await api.evolve.listStageSkills()
      setItems(result.items)
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Stage Skill 加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const groups = useMemo(() => {
    const result = new Map<string, EvolveStageSkill[]>()
    for (const item of items) {
      result.set(item.stageSkillId, [...(result.get(item.stageSkillId) ?? []), item])
    }
    return [...result.entries()].map(([id, versions]) => ({
      id,
      versions: versions.sort((a, b) => Number(b.version.slice(1)) - Number(a.version.slice(1))),
    }))
  }, [items])

  const remove = async (implementation: EvolveStageSkill) => {
    if (!window.confirm('确认删除 ' + implementation.displayName + ' ' + implementation.version + '？已冻结任务仍可继续运行。')) return
    try {
      await api.evolve.deleteStageSkill(implementation.implementationId)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '删除失败')
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-blue-600">Evolve · Stage 扩展</p>
          <h1 className="mt-1 text-2xl font-semibold text-gray-950">Stage Skill</h1>
          <p className="mt-1.5 max-w-3xl text-sm leading-6 text-gray-500">
            Stage 定义进化流程中一步的职责、输入输出和平台规则；Stage Skill 实现这一步开放给用户自定义的处理逻辑。
          </p>
        </div>
        <button onClick={() => navigate('/evolve/stage-skills/new')} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700">
          新建 Stage Skill
        </button>
      </div>

      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="grid grid-cols-[minmax(0,1.4fr)_160px_180px_150px] gap-4 border-b border-gray-100 bg-gray-50 px-5 py-3 text-xs font-medium text-gray-500">
          <span>Skill</span><span>用于 Stage</span><span>版本</span><span className="text-right">操作</span>
        </div>
        {groups.map((group) => {
          const currentId = selected[group.id] || group.versions[0]?.implementationId
          const current = group.versions.find((item) => item.implementationId === currentId) ?? group.versions[0]
          if (!current) return null
          return (
            <div key={group.id} className="grid grid-cols-[minmax(0,1.4fr)_160px_180px_150px] items-center gap-4 border-b border-gray-100 px-5 py-4 last:border-b-0">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-gray-900">{current.displayName}</p>
                <p className="mt-1 text-xs text-gray-500">{modeName[current.mode]}</p>
              </div>
              <span className="text-sm text-gray-700">{current.stageName}</span>
              <div>
                <select value={current.implementationId} onChange={(event) => setSelected((value) => ({ ...value, [group.id]: event.target.value }))} className="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm">
                  {group.versions.map((item) => <option key={item.implementationId} value={item.implementationId}>{item.version}</option>)}
                </select>
                <p className={'mt-1 text-[10px] ' + (current.status === 'test_failed' ? 'text-red-600' : 'text-gray-400')}>{statusName[current.status]}</p>
              </div>
              <div className="flex justify-end gap-3 text-xs font-medium">
                <button className="text-blue-600 hover:text-blue-700" onClick={() => navigate('/evolve/stage-skills/' + current.implementationId)}>查看</button>
                <button className="text-blue-600 hover:text-blue-700" onClick={() => navigate('/evolve/stage-skills/new?upgrade=' + group.id)}>升级</button>
                <button className="text-red-600 hover:text-red-700" onClick={() => void remove(current)}>删除</button>
              </div>
            </div>
          )
        })}
        {!loading && groups.length === 0 && <div className="px-5 py-16 text-center text-sm text-gray-400">还没有 Stage Skill，点击右上角开始开发。</div>}
        {loading && <div className="px-5 py-16 text-center text-sm text-gray-400">正在加载…</div>}
      </section>
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </div>
  )
}
