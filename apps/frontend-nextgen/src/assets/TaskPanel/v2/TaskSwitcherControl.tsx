// @asset-migrated: teamclaw 自研资产
/**
 * TaskSwitcherControl —— 副屏内可拖拽的任务切换入口。
 * 默认仅展示悬浮按钮，点击控制顶部 TaskSwitcherBar 显隐；单任务也可查看当前项；拖拽限制在 TaskPanel 内。
 */
import React, { useEffect, useRef, useState } from 'react';

import { TaskSwitcherIcon } from '../icons';
import { C } from '../tokens';
import { TaskSwitcherBar, type TaskSwitcherProps } from './TaskSwitcherBar';

interface DragPosition {
  x: number;
  y: number;
}

interface TaskSwitcherControlProps extends TaskSwitcherProps {
  /** 仅影响首帧；聊天副屏默认仅展示悬浮开关，宿主首次展示由用户点击开关触发。 */
  defaultVisible?: boolean;
}

interface TaskSwitcherDragState extends DragPosition {
  pointerId: number;
  startClientX: number;
  startClientY: number;
  panelWidth: number;
  panelHeight: number;
  buttonWidth: number;
  buttonHeight: number;
  moved: boolean;
}

const DRAG_MARGIN = 8;
const DRAG_THRESHOLD = 4;

export const TaskSwitcherControl: React.FC<TaskSwitcherControlProps> = (props) => {
  const [visible, setVisible] = useState(props.defaultVisible ?? false);
  const [dragging, setDragging] = useState(false);
  const [position, setPosition] = useState<DragPosition | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const dragRef = useRef<TaskSwitcherDragState | null>(null);
  const suppressClickRef = useRef(false);
  const suppressClickTimerRef = useRef<number | null>(null);
  const hasCustomPosition = position !== null;

  useEffect(
    () => () => {
      if (suppressClickTimerRef.current !== null) {
        window.clearTimeout(suppressClickTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    if (!hasCustomPosition) return undefined;
    const button = buttonRef.current;
    const panel = button?.parentElement?.closest<HTMLElement>('[data-task-panel-root="true"]');
    if (!button || !panel) return undefined;

    const keepButtonInPanel = () => {
      const panelRect = panel.getBoundingClientRect();
      const buttonRect = button.getBoundingClientRect();
      setPosition((current) => {
        if (!current) return current;
        const maxX = Math.max(DRAG_MARGIN, panelRect.width - buttonRect.width - DRAG_MARGIN);
        const maxY = Math.max(DRAG_MARGIN, panelRect.height - buttonRect.height - DRAG_MARGIN);
        const next = {
          x: Math.max(DRAG_MARGIN, Math.min(maxX, current.x)),
          y: Math.max(DRAG_MARGIN, Math.min(maxY, current.y)),
        };
        return next.x === current.x && next.y === current.y ? current : next;
      });
    };

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(keepButtonInPanel);
    observer?.observe(panel);
    window.addEventListener('resize', keepButtonInPanel);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', keepButtonInPanel);
    };
  }, [hasCustomPosition]);

  if (props.items.length === 0) return null;

  const label = visible ? '隐藏任务切换' : '显示任务切换';

  return (
    <>
      {visible ? (
        <TaskSwitcherBar
          items={props.items}
          activeTaskId={props.activeTaskId}
          onSelectTask={props.onSelectTask}
          defaultOpen={props.defaultOpen}
        />
      ) : null}
      <button
        type="button"
        data-task-switcher-control="true"
        title={label}
        aria-label={label}
        aria-pressed={visible}
        ref={buttonRef}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          const button = buttonRef.current;
          const panel = button?.parentElement?.closest<HTMLElement>('[data-task-panel-root="true"]');
          if (!button || !panel) return;

          const buttonRect = button.getBoundingClientRect();
          const panelRect = panel.getBoundingClientRect();
          dragRef.current = {
            pointerId: event.pointerId,
            startClientX: event.clientX,
            startClientY: event.clientY,
            x: buttonRect.left - panelRect.left,
            y: buttonRect.top - panelRect.top,
            panelWidth: panelRect.width,
            panelHeight: panelRect.height,
            buttonWidth: buttonRect.width,
            buttonHeight: buttonRect.height,
            moved: false,
          };
          event.currentTarget.setPointerCapture?.(event.pointerId);
          setDragging(true);
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag || drag.pointerId !== event.pointerId) return;

          const dx = event.clientX - drag.startClientX;
          const dy = event.clientY - drag.startClientY;
          if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
          drag.moved = true;
          event.preventDefault();

          const maxX = Math.max(DRAG_MARGIN, drag.panelWidth - drag.buttonWidth - DRAG_MARGIN);
          const maxY = Math.max(DRAG_MARGIN, drag.panelHeight - drag.buttonHeight - DRAG_MARGIN);
          setPosition({
            x: Math.max(DRAG_MARGIN, Math.min(maxX, drag.x + dx)),
            y: Math.max(DRAG_MARGIN, Math.min(maxY, drag.y + dy)),
          });
        }}
        onPointerUp={(event) => {
          const drag = dragRef.current;
          if (!drag || drag.pointerId !== event.pointerId) return;

          dragRef.current = null;
          setDragging(false);
          event.currentTarget.releasePointerCapture?.(event.pointerId);
          if (drag.moved) {
            suppressClickRef.current = true;
            if (suppressClickTimerRef.current !== null) {
              window.clearTimeout(suppressClickTimerRef.current);
            }
            suppressClickTimerRef.current = window.setTimeout(() => {
              suppressClickRef.current = false;
              suppressClickTimerRef.current = null;
            }, 0);
          }
        }}
        onPointerCancel={(event) => {
          if (dragRef.current?.pointerId !== event.pointerId) return;
          dragRef.current = null;
          setDragging(false);
          event.currentTarget.releasePointerCapture?.(event.pointerId);
        }}
        onClick={() => {
          if (suppressClickRef.current) {
            suppressClickRef.current = false;
            if (suppressClickTimerRef.current !== null) {
              window.clearTimeout(suppressClickTimerRef.current);
              suppressClickTimerRef.current = null;
            }
            return;
          }
          setVisible((current) => !current);
        }}
        style={{
          position: 'absolute',
          right: position ? 'auto' : 16,
          bottom: position ? 'auto' : 24,
          left: position?.x,
          top: position?.y,
          zIndex: 40,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 48,
          height: 48,
          padding: 0,
          border: 'none',
          borderRadius: '50%',
          background: visible ? C.primary : C.textPrimary,
          color: C.surface,
          boxShadow: '0 8px 24px rgba(0,0,0,0.2)',
          cursor: dragging ? 'grabbing' : 'grab',
          touchAction: 'none',
          userSelect: 'none',
          transition: 'background 0.15s, box-shadow 0.15s',
        }}
      >
        <TaskSwitcherIcon size={24} aria-hidden="true" />
      </button>
    </>
  );
};

export default TaskSwitcherControl;
