import BenchTemplatePanel from '../components/BenchTemplatePanel'

export default function BenchTemplates() {
  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <h1 className="text-xl font-semibold text-gray-900">Bench Template</h1>
        <p className="text-base text-gray-500">
          管理从 AgentBench Markdown 用例导入的 Bench Template。
        </p>
      </div>
      <div className="mx-auto w-full max-w-5xl flex-1 overflow-y-auto px-6 py-4">
        <BenchTemplatePanel />
      </div>
    </div>
  )
}
