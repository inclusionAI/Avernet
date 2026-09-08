import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  api,
  type EvolveSkillAsset,
  type EvolveStageCatalog,
  type EvolveStageMode,
  type EvolveStageSkill,
} from '../api/client'
import { useClientUser } from '../hooks/useClientUser'
import type { TCLogBot } from '../types'

const modeName: Record<EvolveStageMode, string> = {
  preprocess: '前置处理',
  postprocess: '后置处理',
  replace: '整体替换',
}

type TestField = { key: string; label: string; type: string; required: boolean }

function testFields(
  stage: EvolveStageCatalog['stages'][number] | undefined,
  mode: EvolveStageMode,
): TestField[] {
  const schema = stage?.inputSchema as {
    required?: string[]
    properties?: Record<string, { type?: string; description?: string }>
  } | undefined
  const fields = Object.entries(schema?.properties ?? {})
    .filter(([key]) => !['task', 'target_skill', 'human_input', 'session_source'].includes(key))
    .map(([key, value]) => ({
      key,
      label: value.description || key,
      type: value.type || 'object',
      required: schema?.required?.includes(key) === true,
    }))
  if (mode === 'postprocess' && stage) {
    fields.push({
      key: 'stage_result',
      label: `平台原有${stage.name}已经生成的结果`,
      type: 'object',
      required: true,
    })
  }
  return fields
}

function buildCase(fields: TestField[], values: Record<string, string>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const field of fields) {
    const raw = values[field.key]?.trim() ?? ''
    if (!raw) continue
    if (field.type === 'number' || field.type === 'integer') result[field.key] = Number(raw)
    else if (field.type === 'boolean') result[field.key] = raw === 'true'
    else if (field.type === 'object' || field.type === 'array') result[field.key] = JSON.parse(raw)
    else result[field.key] = raw
  }
  return result
}

export default function StageSkillDevelopment() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user } = useClientUser()
  const upgradeId = new URLSearchParams(location.search).get('upgrade') ?? ''
  const [catalog, setCatalog] = useState<EvolveStageCatalog | null>(null)
  const [bots, setBots] = useState<TCLogBot[]>([])
  const [assets, setAssets] = useState<EvolveSkillAsset[]>([])
  const [stage, setStage] = useState('diagnose')
  const [mode, setMode] = useState<EvolveStageMode>('preprocess')
  const [name, setName] = useState('')
  const [showGuide, setShowGuide] = useState(false)
  const [packageFile, setPackageFile] = useState<File | null>(null)
  const [implementation, setImplementation] = useState<EvolveStageSkill | null>(null)
  const [botId, setBotId] = useState('')
  const [targetSkillAssetId, setTargetSkillAssetId] = useState('')
  const [caseValues, setCaseValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([
      api.evolve.stageCatalog(),
      api.evolve.listStageSkills(),
      api.evolve.listSkillAssets(),
      user?.userId ? api.tclog.bots({ ownerId: user.userId, status: 'all' }) : Promise.resolve({ bots: [] as TCLogBot[] }),
    ]).then(([stageCatalog, records, skillAssets, botResult]) => {
      if (!active) return
      setCatalog(stageCatalog)
      setAssets(skillAssets.items)
      setBots(botResult.bots)
      if (upgradeId) {
        const latest = records.items
          .filter((item) => item.stageSkillId === upgradeId)
          .sort((a, b) => Number(b.version.slice(1)) - Number(a.version.slice(1)))[0]
        if (latest) {
          setStage(latest.stage)
          setMode(latest.mode)
          setName(latest.displayName)
        }
      }
    }).catch((reason) => setError(reason instanceof Error ? reason.message : '开发信息加载失败'))
    return () => { active = false }
  }, [upgradeId, user?.userId])

  const stageDefinition = catalog?.stages.find((item) => item.stage === stage)
  const fields = useMemo(() => testFields(stageDefinition, mode), [stageDefinition, mode])
  const selectedBot = bots.find((item) => item.botId === botId)
  const botAssets = assets.filter((item) => item.botId === botId)
  const locked = Boolean(upgradeId)

  const download = async () => {
    setBusy('download')
    setError('')
    try {
      const blob = await api.evolve.downloadStageDeveloperPackage(stage, mode)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = stage + '-' + mode + '-stage-skill.zip'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '开发包下载失败')
    } finally {
      setBusy('')
    }
  }

  const upload = async () => {
    if (!name.trim() || !packageFile) { setError('请填写名称并选择开发完成的 ZIP'); return }
    setBusy('upload')
    setError('')
    try {
      const created = await api.evolve.uploadStageSkill({
        stage,
        mode,
        displayName: name.trim(),
        stageSkillId: upgradeId || undefined,
        package: packageFile,
      })
      setImplementation(created)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '上传或静态校验失败')
    } finally {
      setBusy('')
    }
  }

  const runTest = async () => {
    if (!implementation || !botId) { setError('请先完成静态校验并选择测试 Bot'); return }
    setBusy('test')
    setError('')
    try {
      const caseInput = buildCase(fields, caseValues)
      const missing = fields.find((field) => field.required && caseInput[field.key] == null)
      if (missing) throw new Error('请填写：' + missing.label)
      const result = await api.evolve.runStageSkillTest(implementation.implementationId, {
        botId,
        botEnv: selectedBot?.env ?? undefined,
        caseInput,
        targetSkillAssetId: targetSkillAssetId || undefined,
      })
      setImplementation({ ...implementation, status: 'testing', integrationTestTaskId: result.taskId })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '集成测试启动失败')
    } finally {
      setBusy('')
    }
  }

  const register = async () => {
    if (!implementation) return
    setBusy('register')
    setError('')
    try {
      const registered = await api.evolve.registerStageSkill(implementation.implementationId)
      setImplementation(registered)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '注册失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-7 sm:px-6 lg:px-8">
      <button onClick={() => navigate('/evolve/stage-skills')} className="mb-5 text-sm text-gray-500 hover:text-gray-800">← 返回 Stage Skill</button>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-blue-600">Evolve · Stage 扩展</p>
          <h1 className="mt-1 text-2xl font-semibold text-gray-950">{locked ? '升级 Stage Skill' : '新建 Stage Skill'}</h1>
          <p className="mt-1.5 text-sm text-gray-500">选择接入位置，下载开发说明；完成后上传 ZIP，按需运行真实集成测试并注册。</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowGuide(true)} className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50">查看开发说明</button>
          <button disabled={busy === 'download'} onClick={() => void download()} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">下载开发包</button>
        </div>
      </div>

      <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="grid gap-4 md:grid-cols-3">
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-600">用于哪一步</span>
            <select value={stage} disabled={locked} onChange={(event) => setStage(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50">
              {catalog?.stages.map((item) => <option key={item.stage} value={item.stage}>{item.name}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-600">接入位置</span>
            <select value={mode} disabled={locked} onChange={(event) => setMode(event.target.value as EvolveStageMode)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50">
              {stageDefinition?.extensionModes.map((item) => <option key={item} value={item}>{modeName[item]}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-600">Skill 名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：Skill 结构检查" className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-500" />
          </label>
        </div>
        {stageDefinition && <p className="mt-4 rounded-lg bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">{stageDefinition.description}</p>}
      </section>

      <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">1</span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-gray-900">上传开发结果并静态校验</h2>
            <p className="mt-1 text-xs leading-5 text-gray-500">平台确定性检查 ZIP 安全、声明文件、Stage 绑定和 Skill 入口文件，不使用 LLM 判断代码质量。</p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <input type="file" accept=".zip,application/zip" onChange={(event) => setPackageFile(event.target.files?.[0] ?? null)} className="min-w-0 flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm" />
              <button disabled={busy === 'upload'} onClick={() => void upload()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">上传并校验</button>
            </div>
            {implementation?.staticValidation.checks && (
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {implementation.staticValidation.checks.map((check) => <div key={check.id} className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">✓ {check.label}</div>)}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">2</span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-gray-900">运行真实集成测试 <span className="text-sm font-normal text-gray-400">（可选）</span></h2>
            <p className="mt-1 text-xs leading-5 text-gray-500">选择自己的 Bot，平台按当前 Stage 协议组装输入并启动真实 Agent；如 Skill 返回交互问题，可在任务详情中回答后继续同一步。</p>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label><span className="mb-1.5 block text-xs font-medium text-gray-600">测试 Bot</span><select value={botId} onChange={(event) => { setBotId(event.target.value); setTargetSkillAssetId('') }} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option value="">请选择 Bot</option>{bots.map((bot) => <option key={bot.botId + ':' + (bot.env ?? '')} value={bot.botId}>{bot.botName || bot.botId}</option>)}</select></label>
              <label><span className="mb-1.5 block text-xs font-medium text-gray-600">测试目标 Skill <span className="font-normal text-gray-400">（如本实现需要修改 Skill）</span></span><select value={targetSkillAssetId} onChange={(event) => setTargetSkillAssetId(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option value="">不提供目标 Skill</option>{botAssets.map((asset) => <option key={asset.assetId} value={asset.assetId}>{asset.name} · {asset.currentVersion}</option>)}</select></label>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              {fields.map((field) => <label key={field.key} className={field.type === 'object' || field.type === 'array' ? 'md:col-span-2' : ''}><span className="mb-1.5 block text-xs font-medium text-gray-600">{field.label}{field.required && <span className="text-red-500"> *</span>}</span>{field.type === 'object' || field.type === 'array' ? <textarea value={caseValues[field.key] ?? ''} onChange={(event) => setCaseValues((value) => ({ ...value, [field.key]: event.target.value }))} placeholder="填写 JSON 内容" className="min-h-20 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500" /> : <input value={caseValues[field.key] ?? ''} onChange={(event) => setCaseValues((value) => ({ ...value, [field.key]: event.target.value }))} className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-500" />}</label>)}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button disabled={!implementation || busy === 'test'} onClick={() => void runTest()} className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 disabled:opacity-40">启动真实集成测试</button>
              {implementation?.integrationTestTaskId && <button onClick={() => navigate('/evolve/runs/' + implementation.integrationTestTaskId)} className="text-xs font-medium text-blue-600">查看测试任务 ↗</button>}
            </div>
          </div>
        </div>
      </section>

      <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">3</span>
          <div className="min-w-0 flex-1"><h2 className="text-base font-semibold text-gray-900">注册为可用版本</h2><p className="mt-1 text-xs text-gray-500">注册后，创建进化任务时可以在对应 Stage 和接入位置选择这个精确版本。</p></div>
          <button disabled={!implementation || implementation.status === 'testing' || implementation.status === 'registered' || busy === 'register'} onClick={() => void register()} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40">{implementation?.status === 'registered' ? '已注册' : '注册版本'}</button>
        </div>
      </section>
      {error && <p className="mt-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {showGuide && <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/30 p-4" onClick={() => setShowGuide(false)}><div className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl" onClick={(event) => event.stopPropagation()}><div className="flex items-start justify-between gap-4"><div><h2 className="text-xl font-semibold text-gray-950">{stageDefinition?.name} · {modeName[mode]}</h2><p className="mt-2 text-sm leading-6 text-gray-600">{stageDefinition?.description}</p></div><button onClick={() => setShowGuide(false)} className="text-gray-400">关闭</button></div><div className="mt-5 space-y-4 text-sm leading-6 text-gray-700"><p><strong>所在流程：</strong>{catalog?.template.steps.map((item) => item.name).join(' → ')}</p><p><strong>平台负责：</strong>准备任务信息、上游结果和资源目录，处理用户交互的暂停与恢复，并校验最终结果。</p><p><strong>你要开发：</strong>仅完成当前 Stage 在“{modeName[mode]}”位置开放的业务处理，不自行推进下一 Stage 或下一轮。</p><p><strong>开发方式：</strong>下载开发包，把包交给本地 Agent 并提示它完整阅读开发说明；完成后将目录压缩为 ZIP 上传。</p><p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">给本地 Agent 的指令：请完整阅读开发包中的 AGENT_TASK.md，按要求完成这个 Stage 的 Skill，并生成可上传的 ZIP。</p></div></div></div>}
    </div>
  )
}
