import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import PackDiff, { patchRows } from '../PackDiff'
it('renders actual Git hunks and distinct added/deleted line numbers', () => {
  const patch = 'diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -20,2 +20,2 @@\n context\n-old\n+new\n'
  expect(patchRows(patch).slice(1)).toMatchObject([{ before: '20', after: '20', kind: 'context' }, { before: '21', after: '', kind: 'removed' }, { before: '', after: '21', kind: 'added' }])
  render(<PackDiff file={{ path: 'a', status: 'M', before: null, after: null, binary: false, truncated: false, patch }} />)
  expect(screen.getByRole('table', { name: '文件差异 a' })).toBeInTheDocument()
  expect(screen.getByText('-old').closest('tr')).toHaveClass('bg-destructive/10')
  expect(screen.getByText('+new').closest('tr')).toHaveClass('bg-success/10')
  expect(screen.getByText('context').closest('tr')).not.toHaveClass('bg-success/10')
})
it('shows execution-mode changes even when no text line changed', () => {
  render(<PackDiff file={{ path: 'script.sh', status: 'M', before: null, after: null, binary: false, truncated: false,
    patch: 'diff --git a/script.sh b/script.sh\nold mode 100644\nnew mode 100755\n' }} />)
  expect(screen.getByLabelText('文件属性变更')).toHaveTextContent('new mode 100755')
})
