import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/AlertDialog';
import { useDetailFocusRestore } from '@/hooks/taskEscortWorkspace/useDetailFocus';
import type { EditorState } from '@/hooks/taskEscortWorkspace/useTaskEscortWorkspaceEditor';
import { Check, CircleX } from 'lucide-react';
import { Button, Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from './ui';

/** This is triggerless (router/Select-driven), so explicitly restore focus via the project Hook. */
function EditorLeaveDialog({ editor }: { editor: EditorState }) {
  const focusRestore = useDetailFocusRestore();
  return (
    <AlertDialog
      open={editor.leave !== null}
      onOpenChange={(open) => {
        if (!open) editor.cancelLeave();
      }}
    >
      <AlertDialogContent {...focusRestore}>
        <AlertDialogHeader>
          <AlertDialogTitle>有未保存的草稿</AlertDialogTitle>
          <AlertDialogDescription>离开编辑器将丢弃本次未保存的演示修改。</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>继续编辑</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={(event) => {
              event.preventDefault();
              editor.discardAndLeave();
            }}
          >
            放弃并离开
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function EditorDialogs({ editor }: { editor: EditorState }) {
  return (
    <>
      <Modal open={editor.publish} onOpenChange={editor.setPublish}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>模拟发布工作流</ModalTitle>
            <ModalDescription>确认本次配置变更。该操作只更新本地演示版本，不创建真实发布单。</ModalDescription>
          </ModalHeader>
          <div className="rounded-lg border border-border p-4">
            <p className="font-medium">{editor.workflow?.name}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              {editor.version} → v2.5 · {editor.nodes.length} 个节点
            </p>
            <div className="mt-4 space-y-2 text-xs">
              {editor.validation.checks.map((check) => (
                <p
                  key={check.label}
                  data-valid={check.valid}
                  className={`flex items-center gap-2 ${check.valid ? 'text-success' : 'text-destructive'}`}
                >
                  {check.valid ? <Check size={14} aria-hidden /> : <CircleX size={14} aria-hidden />}
                  {check.label} · {check.valid ? '通过' : '未通过'}（演示）
                </p>
              ))}
            </div>
            {!editor.validation.valid && (
              <ul role="alert" className="mt-4 space-y-1 text-xs text-destructive">
                {editor.validation.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </div>
          <p className="text-xs leading-6 text-muted-foreground">
            发布完成不等于效果已验证，仍需观察运行样本并人工确认。
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => editor.setPublish(false)}>
              取消
            </Button>
            <Button disabled={!editor.validation.valid} onClick={editor.publishDraft}>
              确认模拟发布
            </Button>
          </div>
        </ModalContent>
      </Modal>
      <EditorLeaveDialog editor={editor} />
    </>
  );
}
