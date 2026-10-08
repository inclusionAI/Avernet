import Button from '@/components/Button';
import {
  Modal,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalTitle,
} from '@/components/ui/modal';
import { cn } from '@/utils/utils';
import React, { useEffect, useState } from 'react';

interface ServiceInvocationSessionModalProps {
  open: boolean;
  loading?: boolean;
  defaultQuery?: string;
  onClose: () => void;
  onConfirm: (values: { title: string; query: string }) => Promise<boolean>;
}

const ServiceInvocationSessionModal: React.FC<
  ServiceInvocationSessionModalProps
> = ({ open, loading = false, defaultQuery = '', onClose, onConfirm }) => {
  const [title, setTitle] = useState('新会话');
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (open) {
      setTitle('新会话');
      setQuery(defaultQuery.trim() ? defaultQuery : '');
      setError('');
    }
  }, [open, defaultQuery]);

  const handleConfirm = async () => {
    const trimmedTitle = title.trim();
    const trimmedQuery = query.trim();

    if (!trimmedTitle) {
      setError('请输入会话标题');
      return;
    }

    if (!trimmedQuery) {
      setError('请输入协作目标');
      return;
    }

    setError('');
    const success = await onConfirm({
      title: trimmedTitle,
      query: trimmedQuery,
    });
    if (success) {
      onClose();
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && !loading) {
          onClose();
        }
      }}
    >
      <ModalContent size="lg" className="p-0 overflow-hidden">
        <ModalHeader className="mb-0 border-b border-slate-100 px-5 py-4 pr-10">
          <ModalTitle>新建会话</ModalTitle>
        </ModalHeader>

        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              会话标题
              <span className="text-red-500 ml-0.5">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
              className={cn(
                'w-full px-3 py-2 text-sm bg-white border rounded-lg transition-all',
                'focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400',
                error && !title.trim() ? 'border-red-300' : 'border-slate-200',
              )}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              协作目标
              <span className="text-red-500 ml-0.5">*</span>
            </label>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              rows={5}
              className={cn(
                'w-full px-3 py-2 text-sm bg-white border rounded-lg transition-all resize-y',
                'focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400',
                error && !query.trim() ? 'border-red-300' : 'border-slate-200',
              )}
            />
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}
        </div>

        <ModalFooter className="mt-0 border-t border-slate-100 px-5 py-3">
          <Button variant="default" ghost onClick={onClose} disabled={loading}>
            取消
          </Button>
          <Button variant="primary" loading={loading} onClick={handleConfirm}>
            确认
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
};

export default ServiceInvocationSessionModal;
