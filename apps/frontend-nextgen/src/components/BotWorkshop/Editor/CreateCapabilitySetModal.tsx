import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { useRef, useState } from 'react';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (name: string) => Promise<void>;
}

export function CreateCapabilitySetModal({ open, onOpenChange, onCreate }: Props) {
  const [name, setName] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const changeOpen = (value: boolean) => {
    if (!inFlight.current) onOpenChange(value);
  };
  const create = async () => {
    if (inFlight.current || !name.trim()) return;
    // ref 同步互斥，避免 React 重渲染前的连续点击重复提交。
    inFlight.current = true;
    setPending(true);
    setError('');
    try {
      await onCreate(name.trim());
      setName('');
      onOpenChange(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建失败，请重试');
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };
  return (
    <Modal open={open} onOpenChange={changeOpen}>
      <ModalContent>
        <ModalHeader>
          <ModalTitle>新建能力集</ModalTitle>
          <ModalDescription>创建后默认为启用状态。</ModalDescription>
        </ModalHeader>
        <Input autoFocus value={name} maxLength={100} placeholder="输入能力集名称"
          disabled={pending} onChange={(event) => setName(event.target.value)} />
        {pending ? <p role="status" className="text-sm text-muted-foreground">正在创建，请稍候…</p> : null}
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        <ModalFooter>
          <Button variant="secondary" disabled={pending} onClick={() => changeOpen(false)}>取消</Button>
          <Button disabled={pending || !name.trim()} loading={pending} onClick={() => void create()}>
            {pending ? '正在创建…' : '创建'}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
