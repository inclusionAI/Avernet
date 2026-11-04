import React, { useEffect, useRef } from 'react';
import styled from 'styled-components';
import type { GameResult } from './gameResult';

const Backdrop=styled.div`position:absolute;inset:0;z-index:1100;display:grid;place-items:center;padding:16px;background:#05070ce6;backdrop-filter:blur(7px);`;
const Dialog=styled.section`position:relative;display:flex;flex-direction:column;width:min(470px,100%);height:min(580px,100%);max-height:100%;container:finale / size;min-height:0;overflow:hidden;border:2px solid #8e6531;border-radius:8px;color:#f2e7ce;background:radial-gradient(ellipse at 50% 0,#2b211566,transparent 62%),linear-gradient(180deg,#121f2f 0%,#101b2a 100%);box-shadow:inset 0 0 0 2px #080a10,0 20px 70px #000a;`;
const Hero=styled.header`flex-shrink:0;text-align:center;padding:22px 20px 16px;border-bottom:1px solid #8e653155;p{margin:8px 0;color:#8f9aab;font-size:12px}h2{margin:8px 0;font-size:26px;letter-spacing:.04em;line-height:1.3;color:#ffe7b0;text-shadow:0 2px 0 #5e4320}@container finale (max-height:420px){padding:12px 16px;svg{display:none}h2{font-size:22px;margin:4px 0}p{margin:4px 0}}`;
const Trophy=styled.svg`display:block;width:64px;height:64px;margin:0 auto 12px;image-rendering:pixelated;filter:drop-shadow(0 5px 0 #05070c);`;
const Kicker=styled.div`color:#e8b45e;letter-spacing:.23em;font-size:10px;`;
const Summary=styled.div`flex:1 1 auto;min-height:0;overflow:auto;overscroll-behavior:contain;padding:18px 22px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.85;color:#e5d6b8;`;
const Footer=styled.footer`flex-shrink:0;padding:14px 20px;border-top:1px solid #8e653155;text-align:center;background:#0b0e16aa;button{cursor:pointer;width:100%;min-height:40px;border:2px solid #5e4320;border-radius:4px;background:linear-gradient(180deg,#ffd98a 0%,#e8b45e 46%,#c68e3c 100%);color:#2a1b10;font:inherit;font-weight:800;letter-spacing:.06em}small{display:block;margin-top:9px;color:#8f9aab;font-size:11px}`;

export function GameOverDialog({result,round,onClose}:{result:GameResult;round:number;onClose:()=>void}) {
  const dialogRef=useRef<HTMLElement>(null);
  const closeRef=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    const previous=document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel=dialogRef.current?.closest('[data-panel-viewport]');
    const siblings=panel?.querySelectorAll<HTMLElement>('[data-game-content]')??[];
    closeRef.current?.focus();
    siblings.forEach(node=>node.setAttribute('inert',''));
    return()=>{siblings.forEach(node=>node.removeAttribute('inert'));if(previous?.isConnected&&previous!==document.body)previous.focus();else panel?.querySelector<HTMLElement>('[data-result-trigger]')?.focus()};
  },[]);
  return <Backdrop data-region="game-over-overlay" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}>
    <Dialog ref={dialogRef} role="dialog" aria-modal="true" aria-label="游戏结束" onKeyDown={event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose()}
      if(event.key==='Tab'){
        const controls=dialogRef.current?.querySelectorAll<HTMLElement>('button,[tabindex="0"]');
        if(!controls?.length)return;
        const first=controls[0],last=controls[controls.length-1];
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
      }
    }}>
      <Hero><Trophy viewBox="0 0 32 32" aria-hidden="true" shapeRendering="crispEdges"><path d="M8 3h16v3h6v10h-4v3h-6v5h5v3h3v3H4v-3h3v-3h5v-5H6v-3H2V6h6z" fill="#725237"/><path d="M9 3h14v12h-3v4h-8v-4H9zM4 8h4v6H4zm20 0h4v6h-4zM14 19h4v6h-4zM9 26h14v2H9z" fill="#e5b767"/><path d="M11 5h3v9h-3zM8 28h16v2H8z" fill="#ffe8ad"/><path d="M16 6l2 4h4l-3 3 1 4-4-2-4 2 1-4-3-3h4z" fill="#8a6037"/></Trophy><Kicker>CASE CLOSED · 宴散</Kicker><h2>{result.title}</h2><p>第 {round} 轮 · 夜宴落幕，每一句话都留下了线索</p></Hero>
      <Summary tabIndex={0} aria-label="终局公开复盘">{result.summary||'本局已结束。感谢参与这场推理，回到圆桌查看公开发言。'}</Summary>
      <Footer><button ref={closeRef} type="button" onClick={onClose}>回到圆桌</button><small>想再来一局？请在聊天中新建会话</small></Footer>
    </Dialog>
  </Backdrop>;
}
