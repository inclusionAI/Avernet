/**
 * InviteLinkDialog - 邀请链接生成弹窗
 *
 * 用于群管理和会话管理中生成邀请链接
 * 支持设置链接有效期（TTL），生成后可一键复制或展示二维码
 */

import Button from '@/components/Button';
import { cn } from '@/utils/utils';
import { Check, Copy, Download, QrCode, X } from 'lucide-react';
import QRCode from 'qrcode';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

interface InviteLinkDialogProps {
  open: boolean;
  onClose: () => void;
  /** 邀请链接类型 */
  label: string;
  /** 生成链接的异步方法 */
  onGenerate: (ttlSeconds: number) => Promise<string | null>;
}

const UNIT_OPTIONS = [
  { value: 'second', label: '秒', multiplier: 1 },
  { value: 'minute', label: '分钟', multiplier: 60 },
  { value: 'hour', label: '小时', multiplier: 3600 },
  { value: 'day', label: '天', multiplier: 86400 },
];

const InviteLinkDialog: React.FC<InviteLinkDialogProps> = ({
  open,
  onClose,
  label,
  onGenerate,
}) => {
  const [ttlValue, setTtlValue] = useState('1');
  const [ttlUnit, setTtlUnit] = useState('day');
  const [isGenerating, setIsGenerating] = useState(false);
  const [joinUrl, setJoinUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showQrCode, setShowQrCode] = useState(false);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const getTtlSeconds = useCallback(() => {
    const value = parseInt(ttlValue, 10);
    const unit = UNIT_OPTIONS.find((u) => u.value === ttlUnit);
    return value * (unit?.multiplier ?? 86400);
  }, [ttlValue, ttlUnit]);

  const handleGenerate = useCallback(async () => {
    const ttlNum = parseInt(ttlValue, 10);
    if (!ttlNum || ttlNum <= 0) {
      toast.error('请输入有效的数字');
      return;
    }

    setIsGenerating(true);
    try {
      const url = await onGenerate(getTtlSeconds());
      if (url) {
        setJoinUrl(url);
      }
    } finally {
      setIsGenerating(false);
    }
  }, [ttlValue, onGenerate, getTtlSeconds]);

  const handleCopy = useCallback(async () => {
    if (!joinUrl) return;
    try {
      await navigator.clipboard.writeText(joinUrl);
      setCopied(true);
      toast.success('链接已复制到剪贴板');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('复制失败，请手动复制');
    }
  }, [joinUrl]);

  // 生成二维码
  useEffect(() => {
    if (!showQrCode || !joinUrl || !canvasRef.current) return;
    QRCode.toCanvas(
      canvasRef.current,
      joinUrl,
      { width: 200, margin: 2 },
      (err) => {
        if (err) {
          console.error('[InviteLinkDialog] QR generation failed:', err);
          toast.error('二维码生成失败');
        }
      },
    );
    QRCode.toDataURL(joinUrl, { width: 400, margin: 2 }).then((url) => {
      setQrDataUrl(url);
    });
  }, [showQrCode, joinUrl]);

  const handleDownloadQr = useCallback(() => {
    if (!qrDataUrl) return;
    const link = document.createElement('a');
    link.download = `invite-qrcode-${Date.now()}.png`;
    link.href = qrDataUrl;
    link.click();
  }, [qrDataUrl]);

  const handleClose = () => {
    setTtlValue('1');
    setTtlUnit('day');
    setJoinUrl(null);
    setCopied(false);
    setIsGenerating(false);
    setShowQrCode(false);
    setQrDataUrl(null);
    onClose();
  };

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" />
        <div className="relative w-full max-w-md bg-white rounded-xl shadow-xl">
          {/* 头部 */}
          <div className="flex items-center justify-between px-6 py-3 border-b border-slate-100">
            <h2 className="text-base font-semibold text-slate-800">
              分享 - {label}
            </h2>
            <button
              type="button"
              onClick={handleClose}
              className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
            >
              <X className="w-4 h-4 text-slate-400" />
            </button>
          </div>

          {/* 内容 */}
          <div className="px-6 py-4 space-y-4">
            {/* TTL 输入 */}
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">
                链接有效期
              </label>
              <div className="flex gap-2">
                <input
                  type="number"
                  value={ttlValue}
                  onChange={(e) => setTtlValue(e.target.value)}
                  placeholder="1"
                  min="1"
                  className="flex-1 px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all min-w-0"
                />
                <select
                  value={ttlUnit}
                  onChange={(e) => setTtlUnit(e.target.value)}
                  className="px-3 py-2 text-sm bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all appearance-none"
                >
                  {UNIT_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <Button
                  variant="primary"
                  onClick={handleGenerate}
                  loading={isGenerating}
                  disabled={isGenerating || !ttlValue.trim()}
                >
                  生成链接
                </Button>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                默认 1 天，可被多人多次使用直到过期
              </p>
            </div>

            {/* 生成的链接 */}
            {joinUrl && (
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  邀请链接
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={joinUrl}
                    readOnly
                    className="flex-1 px-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-lg text-slate-600 truncate"
                  />
                  <button
                    type="button"
                    onClick={handleCopy}
                    className={cn(
                      'flex items-center gap-1 px-3 py-2 text-xs font-medium rounded-lg transition-colors flex-shrink-0',
                      copied
                        ? 'bg-green-50 text-green-600 border border-green-200'
                        : 'bg-lavender-50 text-lavender-600 border border-lavender-200 hover:bg-lavender-100',
                    )}
                  >
                    {copied ? (
                      <>
                        <Check className="w-3.5 h-3.5" />
                        已复制
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        复制链接
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowQrCode(true)}
                    className="flex items-center gap-1 px-3 py-2 text-xs font-medium rounded-lg transition-colors flex-shrink-0 bg-slate-50 text-slate-600 border border-slate-200 hover:bg-slate-100"
                  >
                    <QrCode className="w-3.5 h-3.5" />
                    二维码
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 底部 */}
          <div className="flex items-center justify-end px-6 py-3 border-t border-slate-100">
            <Button variant="default" ghost onClick={handleClose}>
              关闭
            </Button>
          </div>
        </div>
      </div>

      {/* 二维码弹窗 */}
      {showQrCode && joinUrl && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/30"
            onClick={() => setShowQrCode(false)}
          />
          <div className="relative bg-white rounded-xl shadow-xl p-6 flex flex-col items-center gap-4">
            <div className="flex items-center justify-between w-full">
              <h3 className="text-sm font-semibold text-slate-800">扫码加入</h3>
              <button
                type="button"
                onClick={() => setShowQrCode(false)}
                className="p-1 rounded-lg hover:bg-slate-100 transition-colors"
              >
                <X className="w-4 h-4 text-slate-400" />
              </button>
            </div>
            <canvas ref={canvasRef} className="rounded-lg" />
            <button
              type="button"
              onClick={handleDownloadQr}
              disabled={!qrDataUrl}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-lavender-600 bg-lavender-50 border border-lavender-200 rounded-lg hover:bg-lavender-100 transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              下载二维码
            </button>
          </div>
        </div>
      )}
    </>
  );
};

export default InviteLinkDialog;
