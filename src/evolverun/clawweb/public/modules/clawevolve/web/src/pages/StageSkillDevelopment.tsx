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

const modeDescription: Record<EvolveStageMode, string> = {
  preprocess: '在平台原有处理开始前执行，用于检查输入、补充上下文或准备候选资源；完成后继续平台原有处理。',
  postprocess: '在平台原有处理完成后执行，用于复核、整理或补充结果；你的交付将成为这一步的最终结果。',
  replace: '不运行平台原有处理，由你的实现完成这一整个 Stage，并把最终结果交给模板的下一步。',
}

const agentPrompt = '请完整阅读开发包中的 AGENT_TASK.md，按照其中的模板背景、Stage 职责、接入位置和 Contract 要求完成自定义实现，并生成可上传的 ZIP。'

type TestField = { key: string; label: string; type: string; required: boolean }

function schemaFields(schemaValue: unknown): TestField[] {
  const schema = schemaValue as {
    required?: string[]
    properties?: Record<string, { type?: string; description?: string }>
  } | undefined
  return Object.entries(schema?.properties ?? {}).map(([key, value]) => ({
    key,
    label: value.description || key,
    type: value.type || 'object',
    required: schema?.required?.includes(key) === true,
  }))
}

function testFields(
  stage: EvolveStageCatalog['stages'][number] | undefined,
  mode: EvolveStageMode,
): TestField[] {
  const fields = schemaFields(stage?.inputSchema)
    .filter(({ key }) => !['task', 'target_skill', 'human_input', 'session_source'].includes(key))
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

function FieldList({ fields }: { fields: TestField[] }) {
  return <div className="mt-3 divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-200 bg-white">
    {fields.map((field) => <div key={field.key} className="grid gap-1 px-4 py-3 sm:grid-cols-[170px_minmax(0,1fr)] sm:gap-4">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs font-semibold text-blue-700">{field.key}</span>
        {field.required && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-600">必需</span>}
      </div>
      <p className="text-xs leading-5 text-gray-600">{field.label}<span className="ml-2 text-gray-400">{field.type}</span></p>
    </div>)}
  </div>
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
  const [showGuide, setShowGuide] = useState(false)
  const [showIntegrationTest, setShowIntegrationTest] = useState(false)
  const [copied, setCopied] = useState(false)
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
        }
      } else {
        const firstOpenStage = stageCatalog.stages.find((item) => item.extensionModes.length > 0)
        if (firstOpenStage) {
          setStage(firstOpenStage.stage)
          setMode(firstOpenStage.extensionModes[0])
        }
      }
    }).catch((reason) => setError(reason instanceof Error ? reason.message : '开发信息加载失败'))
    return () => { active = false }
  }, [upgradeId, user?.userId])

  const openStages = catalog?.stages.filter((item) => item.extensionModes.length > 0) ?? []
  const stageDefinition = openStages.find((item) => item.stage === stage)
  const inputFields = schemaFields(stageDefinition?.inputSchema)
  const outputFields = mode === 'preprocess'
    ? [{ key: 'summary', label: '说明本次前置处理完成了什么', type: 'string', required: true }]
    : schemaFields(stageDefinition?.resultSchema)
  const fields = useMemo(() => testFields(stageDefinition, mode), [stageDefinition, mode])
  const selectedBot = bots.find((item) => item.botId === botId)
  const botAssets = assets.filter((item) => item.botId === botId)
  const locked = Boolean(upgradeId)

  const selectStage = (nextStage: string) => {
    setStage(nextStage)
    const definition = openStages.find((item) => item.stage === nextStage)
    if (definition?.extensionModes[0]) setMode(definition.extensionModes[0])
    setImplementation(null)
    setPackageFile(null)
    setCaseValues({})
  }

  const download = async () => {
    setBusy('download')
    setError('')
    try {
      const blob = await api.evolve.downloadStageDeveloperPackage(stage, mode)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${stage}-${mode}-custom-stage.zip`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '开发包下载失败')
    } finally {
      setBusy('')
    }
  }

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(agentPrompt)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setError('提示词复制失败，请手动复制')
    }
  }

  const upload = async () => {
    if (!packageFile) { setError('请选择开发完成的 ZIP'); return }
    setBusy('upload')
    setError('')
    try {
      const created = await api.evolve.uploadStageSkill({
        stage,
        mode,
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
      <button onClick={() => navigate('/evolve/stage-skills')} className="mb-5 text-sm text-gray-500 hover:text-gray-800">← 返回自定义 Stage</button>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-blue-600">Evolve · 自定义 Stage</p>
          <h1 className="mt-1 text-2xl font-semibold text-gray-950">{locked ? '升级自定义实现' : '接入自定义实现'}</h1>
          <p className="mt-1.5 max-w-3xl text-sm leading-6 text-gray-500">Stage 是进化模板中的处理步骤，平台已经定义它的职责和运行规则。你在开放的位置接入自己的 Skill 实现，不会创建新的 Stage，也不会新建业务 Skill。</p>
        </div>
        <button onClick={() => setShowGuide(true)} className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50">查看开发说明</button>
      </div>

      <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900">选择开放位置</h2>
        <p className="mt-1 text-xs leading-5 text-gray-500">先确认模板，再选择模板中允许接入自定义实现的 Stage 和位置。</p>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-600">模板</span>
            <select value={catalog?.template.name ?? ''} disabled className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-700">
              {catalog && <option value={catalog.template.name}>{catalog.template.name}</option>}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-600">开放的 Stage</span>
            <select value={stage} disabled={locked} onChange={(event) => selectStage(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50">
              {openStages.map((item) => <option key={item.stage} value={item.stage}>{item.name}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-600">接入位置</span>
            <select value={mode} disabled={locked} onChange={(event) => { setMode(event.target.value as EvolveStageMode); setImplementation(null); setPackageFile(null) }} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50">
              {stageDefinition?.extensionModes.map((item) => <option key={item} value={item}>{modeName[item]}</option>)}
            </select>
          </label>
        </div>
        {stageDefinition && <div className="mt-4 rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3"><p className="text-sm font-medium text-blue-950">{stageDefinition.name} · {modeName[mode]}</p><p className="mt-1 text-xs leading-5 text-blue-800">{stageDefinition.description} {modeDescription[mode]}</p></div>}
      </section>

      <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">1</span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-gray-900">下载开发包</h2>
            <p className="mt-1 text-xs leading-5 text-gray-500">平台根据“{catalog?.template.name ?? '当前模板'} / {stageDefinition?.name ?? '当前 Stage'} / {modeName[mode]}”生成开发包，包含完整开发说明、Contract、声明文件和实现入口。</p>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button disabled={!catalog || busy === 'download'} onClick={() => void download()} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{busy === 'download' ? '正在生成…' : '下载开发包'}</button>
              <button onClick={() => setShowGuide(true)} className="text-xs font-medium text-blue-600 hover:text-blue-700">先查看完整开发说明 ↗</button>
            </div>
            <div className="mt-4 rounded-xl border border-gray-200 bg-gray-50 p-4">
              <div className="flex items-center justify-between gap-3"><p className="text-xs font-semibold text-gray-700">给本地 Agent 的标准提示词</p><button onClick={() => void copyPrompt()} className="shrink-0 text-xs font-medium text-blue-600">{copied ? '已复制' : '复制'}</button></div>
              <p className="mt-2 select-all text-xs leading-5 text-gray-600">{agentPrompt}</p>
            </div>
          </div>
        </div>
      </section>

      <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-start gap-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">2</span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-gray-900">上传开发结果并静态校验</h2>
            <p className="mt-1 text-xs leading-5 text-gray-500">检查 ZIP 安全、声明文件、Stage 绑定和 Skill 入口文件。</p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <input type="file" accept=".zip,application/zip" onChange={(event) => setPackageFile(event.target.files?.[0] ?? null)} className="min-w-0 flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm" />
              <button disabled={busy === 'upload'} onClick={() => void upload()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">上传并校验</button>
            </div>
            {implementation && <p className="mt-3 text-xs text-gray-500">已从声明文件识别：<span className="font-medium text-gray-800">{implementation.displayName}</span> · {implementation.version}</p>}
            {implementation?.staticValidation.checks && (
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                {implementation.staticValidation.checks.map((check) => <div key={check.id} className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">✓ {check.label}</div>)}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="mt-5 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <button type="button" onClick={() => setShowIntegrationTest((value) => !value)} className="flex w-full items-center gap-4 p-5 text-left">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">3</span>
          <span className="min-w-0 flex-1"><span className="block text-base font-semibold text-gray-900">运行真实集成测试 <span className="text-sm font-normal text-gray-400">（可选）</span></span><span className="mt-1 block text-xs leading-5 text-gray-500">主动展开后选择自己的 Bot 和测试 Case，验证真实输入下发、Agent 执行、交互与结果交付。</span></span>
          <span className="text-sm text-gray-400">{showIntegrationTest ? '收起' : '展开'}</span>
        </button>
        {showIntegrationTest && <div className="border-t border-gray-100 px-5 pb-5 pt-4 sm:pl-16">
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <label><span className="mb-1.5 block text-xs font-medium text-gray-600">测试 Bot</span><select value={botId} onChange={(event) => { setBotId(event.target.value); setTargetSkillAssetId('') }} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option value="">请选择 Bot</option>{bots.map((bot) => <option key={bot.botId + ':' + (bot.env ?? '')} value={bot.botId}>{bot.botName || bot.botId}</option>)}</select></label>
              <label><span className="mb-1.5 block text-xs font-medium text-gray-600">测试目标 Skill <span className="font-normal text-gray-400">（按当前 Stage 需要选填）</span></span><select value={targetSkillAssetId} onChange={(event) => setTargetSkillAssetId(event.target.value)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm"><option value="">不提供目标 Skill</option>{botAssets.map((asset) => <option key={asset.assetId} value={asset.assetId}>{asset.name} · {asset.currentVersion}</option>)}</select></label>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              {fields.map((field) => <label key={field.key} className={field.type === 'object' || field.type === 'array' ? 'md:col-span-2' : ''}><span className="mb-1.5 block text-xs font-medium text-gray-600">{field.label}{field.required && <span className="text-red-500"> *</span>}</span>{field.type === 'object' || field.type === 'array' ? <textarea value={caseValues[field.key] ?? ''} onChange={(event) => setCaseValues((value) => ({ ...value, [field.key]: event.target.value }))} placeholder="填写 JSON 内容" className="min-h-20 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500" /> : <input value={caseValues[field.key] ?? ''} onChange={(event) => setCaseValues((value) => ({ ...value, [field.key]: event.target.value }))} className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-500" />}</label>)}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button disabled={!implementation || busy === 'test'} onClick={() => void runTest()} className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 disabled:opacity-40">启动真实集成测试</button>
              {implementation?.integrationTestTaskId && <button onClick={() => navigate('/evolve/runs/' + implementation.integrationTestTaskId)} className="text-xs font-medium text-blue-600">查看测试任务 ↗</button>}
            </div>
        </div>}
      </section>

      <section className="mt-5 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-4">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600 text-sm font-semibold text-white">4</span>
          <div className="min-w-0 flex-1"><h2 className="text-base font-semibold text-gray-900">注册为可用版本</h2><p className="mt-1 text-xs text-gray-500">注册后，创建进化任务时可在对应 Stage 中选择这个精确版本。</p></div>
          <button disabled={!implementation || implementation.status === 'testing' || implementation.status === 'registered' || busy === 'register'} onClick={() => void register()} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-40">{implementation?.status === 'registered' ? '已注册' : '注册版本'}</button>
        </div>
      </section>
      {error && <p className="mt-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {showGuide && <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/30 p-4" onClick={() => setShowGuide(false)}>
        <div className="max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white shadow-xl" onClick={(event) => event.stopPropagation()}>
          <div className="sticky top-0 flex items-start justify-between gap-4 border-b border-gray-100 bg-white px-6 py-5">
            <div><p className="text-xs font-medium text-blue-600">{catalog?.template.name} · {stageDefinition?.name}</p><h2 className="mt-1 text-xl font-semibold text-gray-950">{modeName[mode]}开发说明</h2></div>
            <button onClick={() => setShowGuide(false)} className="text-sm text-gray-400 hover:text-gray-700">关闭</button>
          </div>
          <div className="space-y-7 px-6 py-6 text-sm leading-6 text-gray-700">
            <section><h3 className="text-base font-semibold text-gray-950">模板背景与当前位置</h3><p className="mt-2 text-sm text-gray-600">{catalog?.template.description}</p><div className="mt-3 flex flex-wrap items-center gap-2">{catalog?.template.steps.map((item, index) => <div key={item.stage} className="flex items-center gap-2"><span className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${item.stage === stage ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-500'}`}>{item.name}</span>{index < (catalog?.template.steps.length ?? 0) - 1 && <span className="text-gray-300">→</span>}</div>)}</div></section>
            <section><h3 className="text-base font-semibold text-gray-950">当前 Stage 与开放位置</h3><p className="mt-2 text-sm text-gray-600"><span className="font-medium text-gray-900">{stageDefinition?.name}：</span>{stageDefinition?.description}</p><p className="mt-2 rounded-xl bg-blue-50 px-4 py-3 text-sm text-blue-900"><span className="font-semibold">{modeName[mode]}：</span>{modeDescription[mode]}</p></section>
            <section><h3 className="text-base font-semibold text-gray-950">运行时会拿到什么</h3><p className="mt-1 text-xs text-gray-500">平台按照 Contract 准备下列信息；路径字段指向本次任务的隔离目录。</p><FieldList fields={inputFields} />{mode === 'postprocess' && <p className="mt-2 text-xs text-gray-500">后置处理还会收到 <span className="font-mono text-blue-700">stage_result</span>，即平台原有 {stageDefinition?.name} 已生成的结果。</p>}</section>
            <section><h3 className="text-base font-semibold text-gray-950">完成后需要交付什么</h3><p className="mt-1 text-xs text-gray-500">信息足够时返回完成结果；平台按下列结构校验后继续模板下一步。</p><FieldList fields={outputFields} /></section>
            <section><h3 className="text-base font-semibold text-gray-950">需要用户补充信息时</h3><p className="mt-2 text-sm text-gray-600">实现可以返回文本问题或动态 HTML 表单。平台在隔离页面中展示内容、收集回答，并把回答加入 <span className="font-mono text-xs">human_input</span> 后再次执行当前 Stage；实现不需要挂起进程等待。</p></section>
            <section><h3 className="text-base font-semibold text-gray-950">平台与自定义实现如何配合</h3><div className="mt-3 grid gap-3 sm:grid-cols-2"><div className="rounded-xl border border-gray-200 p-4"><p className="text-sm font-semibold text-gray-900">平台处理</p><p className="mt-1 text-xs leading-5 text-gray-600">准备真实任务输入和候选资源，按接入位置调用实现，处理用户交互，并校验交付结果后推进流程。</p></div><div className="rounded-xl border border-gray-200 p-4"><p className="text-sm font-semibold text-gray-900">你的实现</p><p className="mt-1 text-xs leading-5 text-gray-600">读取当前输入，完成这个开放位置的业务处理，并按 Contract 返回结果或需要补充的问题。</p></div></div></section>
            <section><h3 className="text-base font-semibold text-gray-950">使用开发包完成开发</h3><ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-gray-600"><li>下载开发包，完整阅读 AGENT_TASK.md、contract.json 和 stage-skill.json。</li><li>把开发包和下方标准提示词交给本地 Agent，在 implementation/ 中完成实现。</li><li>将完成后的目录压缩为 ZIP，回到平台上传；通过静态校验后可运行真实集成测试并注册。</li></ol><div className="mt-3 rounded-xl bg-gray-50 p-4"><div className="flex items-center justify-between gap-3"><p className="text-xs font-semibold text-gray-700">标准提示词</p><button onClick={() => void copyPrompt()} className="text-xs font-medium text-blue-600">{copied ? '已复制' : '复制'}</button></div><p className="mt-2 text-xs leading-5 text-gray-600">{agentPrompt}</p></div></section>
          </div>
        </div>
      </div>}
    </div>
  )
}
