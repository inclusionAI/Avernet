import { useState } from 'react'
import {
  useBenchTemplates,
  usePublishBenchTemplate,
  useImportAgentBench,
  useUpdateBenchTemplate,
} from '../api/hooks'
import type { BenchTemplate } from '../types'
import { useNavigate } from 'react-router-dom'

type FilterState = {
  status: string
  category: string
  targetType: string
  search: string
}

type EditForm = {
  title: string
  description: string
  category: string
  gradingType: string
  contentMd: string
}

function emptyEditForm(t: BenchTemplate): EditForm {
  return {
    title: t.title,
    description: t.description ?? '',
    category: t.category ?? '',
    gradingType: t.gradingType,
    contentMd: t.contentMd,
  }
}

export default function BenchTemplatePanel() {
  const navigate = useNavigate()
  const { data: templates, isLoading } = useBenchTemplates()
  const publishMutation = usePublishBenchTemplate()
  const importMutation = useImportAgentBench()
  const updateMutation = useUpdateBenchTemplate()

  const [filters, setFilters] = useState<FilterState>({
    status: '',
    category: '',
    targetType: '',
    search: '',
  })
  const [showImport, setShowImport] = useState(false)
  const [importForm, setImportForm] = useState({ sourcePath: '', contentMd: '' })
  const [editing, setEditing] = useState<BenchTemplate | null>(null)
  const [editForm, setEditForm] = useState<EditForm>({ title: '', description: '', category: '', gradingType: '', contentMd: '' })
  const [error, setError] = useState<string | null>(null)

  const filtered = (templates ?? []).filter((t) => {
    if (filters.status && t.status !== filters.status) return false
    if (filters.category && t.category !== filters.category) return false
    if (filters.targetType && t.targetType !== filters.targetType) return false
    if (filters.search) {
      const q = filters.search.toLowerCase()
      return (
        t.taskId.toLowerCase().includes(q) ||
        t.title.toLowerCase().includes(q) ||
        (t.description?.toLowerCase().includes(q) ?? false)
      )
    }
    return true
  })

  const categories = Array.from(new Set((templates ?? []).map((t) => t.category).filter(Boolean)))

  const handlePublish = async (templateId: string, version: number) => {
    try {
      setError(null)
      await publishMutation.mutateAsync({ templateId, version })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish template')
    }
  }

  const handleImport = async () => {
    if (!importForm.contentMd.trim()) {
      setError('Content markdown is required')
      return
    }
    try {
      setError(null)
      await importMutation.mutateAsync({
        sourcePath: importForm.sourcePath || undefined,
        contentMd: importForm.contentMd,
      })
      setShowImport(false)
      setImportForm({ sourcePath: '', contentMd: '' })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to import template')
    }
  }

  const startEdit = (t: BenchTemplate) => {
    setEditing(t)
    setEditForm(emptyEditForm(t))
    setError(null)
  }

  const cancelEdit = () => {
    setEditing(null)
    setEditForm({ title: '', description: '', category: '', gradingType: '', contentMd: '' })
  }

  const handleUpdate = async () => {
    if (!editing) return
    if (!editForm.title.trim() || !editForm.contentMd.trim()) {
      setError('Title and Content are required')
      return
    }
    try {
      setError(null)
      await updateMutation.mutateAsync({
        templateId: editing.templateId,
        version: editing.version,
        input: {
          title: editForm.title,
          description: editForm.description || null,
          category: editForm.category || null,
          gradingType: editForm.gradingType,
          contentMd: editForm.contentMd,
        },
      })
      setEditing(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update template')
    }
  }

  const statusBadge = (status: string) => {
    const map: Record<string, string> = {
      draft: 'bg-gray-100 text-gray-700',
      published: 'bg-green-100 text-green-700',
      archived: 'bg-orange-100 text-orange-700',
    }
    return (
      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${map[status] ?? 'bg-gray-100 text-gray-700'}`}>
        {status}
      </span>
    )
  }

  if (isLoading) {
    return <div className="text-gray-500 text-sm">Loading bench templates...</div>
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          placeholder="Search taskId or title..."
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          value={filters.search}
          onChange={(e) => setFilters({ ...filters, search: e.target.value })}
        />
        <select
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
          value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
        >
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="published">Published</option>
          <option value="archived">Archived</option>
        </select>
        <select
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
          value={filters.category}
          onChange={(e) => setFilters({ ...filters, category: e.target.value })}
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
          value={filters.targetType}
          onChange={(e) => setFilters({ ...filters, targetType: e.target.value })}
        >
          <option value="">All targets</option>
          <option value="agent_session">agent_session</option>
        </select>
        <div className="ml-auto">
          <button
            onClick={() => setShowImport(!showImport)}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          >
            {showImport ? 'Cancel' : '+ Import Markdown'}
          </button>
        </div>
      </div>

      {showImport && (
        <div className="rounded-lg border border-gray-200 bg-white p-4 space-y-3">
          <h3 className="text-sm font-semibold text-gray-900">Import AgentBench Markdown</h3>
          <div>
            <label className="block text-sm font-medium text-gray-700">Source Path (optional)</label>
            <input
              type="text"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              value={importForm.sourcePath}
              onChange={(e) => setImportForm({ ...importForm, sourcePath: e.target.value })}
              placeholder="tasks/benchmark/mcp/example.md"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Markdown Content <span className="text-red-500">*</span></label>
            <textarea
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              rows={8}
              value={importForm.contentMd}
              onChange={(e) => setImportForm({ ...importForm, contentMd: e.target.value })}
              placeholder="---&#10;id: my-task&#10;---&#10;# Prompt&#10;..."
            />
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={() => { setShowImport(false); setImportForm({ sourcePath: '', contentMd: '' }); setError(null) }}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleImport}
              disabled={importMutation.isPending}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {importMutation.isPending ? 'Importing...' : 'Import'}
            </button>
          </div>
        </div>
      )}

      {editing && (
        <div className="rounded-lg border border-gray-200 bg-white p-4 space-y-3">
          <h3 className="text-sm font-semibold text-gray-900">Edit Template: {editing.templateId} v{editing.version}</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700">Title <span className="text-red-500">*</span></label>
              <input
                type="text"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                value={editForm.title}
                onChange={(e) => setEditForm({ ...editForm, title: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Grading Type</label>
              <select
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                value={editForm.gradingType}
                onChange={(e) => setEditForm({ ...editForm, gradingType: e.target.value })}
              >
                <option value="automated">automated</option>
                <option value="llm_judge">llm_judge</option>
                <option value="hybrid">hybrid</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700">Category</label>
              <input
                type="text"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                value={editForm.category}
                onChange={(e) => setEditForm({ ...editForm, category: e.target.value })}
                placeholder="mcp"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Task ID</label>
              <input
                type="text"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm bg-gray-50 text-gray-500"
                value={editing.taskId}
                disabled
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Description</label>
            <input
              type="text"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              value={editForm.description}
              onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Markdown Content <span className="text-red-500">*</span></label>
            <textarea
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
              rows={8}
              value={editForm.contentMd}
              onChange={(e) => setEditForm({ ...editForm, contentMd: e.target.value })}
            />
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={cancelEdit}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleUpdate}
              disabled={updateMutation.isPending}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {updateMutation.isPending ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      )}

      {filtered.length === 0 && !showImport && !editing && (
        <div className="text-center py-8 text-gray-500 text-sm">
          No bench templates found. Click &quot;+ Import Markdown&quot; to add one.
        </div>
      )}

      <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium text-gray-700">Task ID</th>
              <th className="px-4 py-2 text-left font-medium text-gray-700">Title</th>
              <th className="px-4 py-2 text-left font-medium text-gray-700">Version</th>
              <th className="px-4 py-2 text-left font-medium text-gray-700">Grading</th>
              <th className="px-4 py-2 text-left font-medium text-gray-700">Status</th>
              <th className="px-4 py-2 text-left font-medium text-gray-700">Updated</th>
              <th className="px-4 py-2 text-right font-medium text-gray-700">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {filtered.map((t) => (
              <tr key={`${t.templateId}-${t.version}`} className="hover:bg-gray-50 cursor-pointer" onClick={() => navigate(`/bench/templates/${t.templateId}`)}>
                <td className="px-4 py-2">
                  <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">{t.taskId}</code>
                </td>
                <td className="px-4 py-2 font-medium text-gray-900">{t.title}</td>
                <td className="px-4 py-2 text-gray-600">v{t.version}</td>
                <td className="px-4 py-2 text-gray-600">{t.gradingType}</td>
                <td className="px-4 py-2">{statusBadge(t.status)}</td>
                <td className="px-4 py-2 text-gray-500 text-xs">
                  {new Date(t.gmtModified * 1000).toLocaleString()}
                </td>
                <td className="px-4 py-2 text-right">
                  <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
                    {t.status === 'draft' && (
                      <>
                        <button
                          onClick={() => startEdit(t)}
                          className="rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => handlePublish(t.templateId, t.version)}
                          disabled={publishMutation.isPending}
                          className="rounded-md border border-green-300 px-2 py-1 text-xs text-green-700 hover:bg-green-50 disabled:opacity-50"
                        >
                          Publish
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
