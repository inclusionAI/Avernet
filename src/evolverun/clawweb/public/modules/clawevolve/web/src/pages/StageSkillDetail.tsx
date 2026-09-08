import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, type EvolveStageSkill } from '../api/client'

const modeName = { preprocess: '前置处理', postprocess: '后置处理', replace: '整体替换' } as const
type PackageContent = Awaited<ReturnType<typeof api.evolve.getStageSkillContent>>

export default function StageSkillDetail() {
  const navigate = useNavigate()
  const location = useLocation()
  const implementationId = decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1) ?? '')
  const [item, setItem] = useState<EvolveStageSkill | null>(null)
  const [packageContent, setPackageContent] = useState<PackageContent | null>(null)
  const [selectedPath, setSelectedPath] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    void Promise.all([
      api.evolve.getStageSkill(implementationId),
      api.evolve.getStageSkillContent(implementationId),
    ]).then(([nextItem, nextContent]) => {
      setItem(nextItem)
      setPackageContent(nextContent)
      setSelectedPath(nextContent.selected?.path ?? '')
    }).catch((reason) => setError(reason instanceof Error ? reason.message : 'Stage Skill 加载失败'))
  }, [implementationId])

  useEffect(() => {
    if (!selectedPath || selectedPath === packageContent?.selected?.path) return
    void api.evolve.getStageSkillContent(implementationId, selectedPath)
      .then(setPackageContent)
      .catch((reason) => setError(reason instanceof Error ? reason.message : 'Skill 文件加载失败'))
  }, [implementationId, packageContent?.selected?.path, selectedPath])

  return (
    <div className="mx-auto max-w-5xl px-4 py-7 sm:px-6 lg:px-8">
      <button onClick={() => navigate('/evolve/stage-skills')} className="mb-5 text-sm text-gray-500">← 返回 Stage Skill</button>
      {item && <>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-blue-600">{item.stageName} · {modeName[item.mode]}</p>
            <h1 className="mt-1 text-2xl font-semibold text-gray-950">{item.displayName}</h1>
            <p className="mt-1 text-sm text-gray-500">{item.version}</p>
          </div>
          <button onClick={() => navigate(`/evolve/stage-skills/new?upgrade=${encodeURIComponent(item.stageSkillId)}`)} className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700">升级</button>
        </div>

        <section className="mt-5 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-100 px-5 py-4">
            <h2 className="text-sm font-semibold text-gray-900">Skill 内容</h2>
            <p className="mt-1 text-xs text-gray-500">查看这个版本实际上传并在运行时执行的文件。</p>
          </div>
          <div className="grid min-h-[360px] md:grid-cols-[240px_minmax(0,1fr)]">
            <aside className="border-r border-gray-100 p-3">
              {packageContent?.files.map((file) => (
                <button
                  key={file.path}
                  disabled={!file.text}
                  onClick={() => setSelectedPath(file.path)}
                  className={`block w-full truncate rounded-lg px-3 py-2 text-left font-mono text-xs ${selectedPath === file.path ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-50'} disabled:text-gray-300`}
                >
                  {file.path}
                </button>
              ))}
            </aside>
            <pre className="min-w-0 overflow-auto bg-[#0b1020] p-5 text-xs leading-6 text-gray-100">
              {packageContent?.selected?.content ?? '请选择可预览的文本文件'}
            </pre>
          </div>
        </section>

        <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-900">静态校验</h2>
              <p className="mt-1 text-xs text-gray-500">确定性检查开发包结构与平台声明。</p>
            </div>
            <span className="text-xs font-medium text-emerald-700">已通过</span>
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {item.staticValidation.checks?.map((check) => <div key={check.id} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-700">✓ {check.label}</div>)}
          </div>
        </section>

        <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-gray-900">真实集成测试</h2>
          {item.integrationTestTaskId
            ? <button className="mt-3 text-sm font-medium text-blue-600" onClick={() => navigate(`/evolve/runs/${item.integrationTestTaskId}`)}>查看测试任务与实际输出 ↗</button>
            : <p className="mt-3 text-sm text-gray-400">当前版本未运行集成测试。</p>}
        </section>
      </>}
      {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    </div>
  )
}
