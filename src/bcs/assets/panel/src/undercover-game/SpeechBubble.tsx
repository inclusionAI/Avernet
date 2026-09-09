import React, { useEffect, useRef } from 'react';
import styled from 'styled-components';
import frame from './assets/speech-frame.svg?raw';
import tail from './assets/speech-tail.svg?raw';
import type { BubblePlacement } from './speechBubbleLayout';

const frameUrl=`data:image/svg+xml,${encodeURIComponent(frame)}`;
const tailUrl=`data:image/svg+xml,${encodeURIComponent(tail)}`;
const Bubble=styled.button<{ $placement:BubblePlacement }>`position:absolute;z-index:200;left:${p=>p.$placement.x}px;top:${p=>p.$placement.y}px;width:${p=>p.$placement.width}px;height:${p=>p.$placement.height}px;border:8px solid transparent;border-image:url("${frameUrl}") 8 fill / 8px / 0 stretch;border-radius:0;padding:2px 5px;background:transparent;color:#322a27;font:inherit;text-align:left;cursor:pointer;filter:drop-shadow(0 3px 0 #171f2b80);animation:bubble-arrive .18s steps(3,end);&:hover{filter:drop-shadow(0 3px 0 #171f2b80) brightness(1.04)}&:focus-visible{outline:2px solid #94dfcb;outline-offset:5px}@keyframes bubble-arrive{from{opacity:0;translate:0 4px}to{opacity:1;translate:0 0}}`;
const Tail=styled.span<{ $placement:BubblePlacement }>`position:absolute;pointer-events:none;width:24px;height:24px;background:url("${tailUrl}") center / 100% 100%;image-rendering:pixelated;${p=>p.$placement.side==='right'?`left:-28px;top:${p.$placement.tail-8}px;`:p.$placement.side==='left'?`right:-28px;top:${p.$placement.tail-8}px;transform:scaleX(-1);`:p.$placement.side==='above'?`bottom:-28px;left:${p.$placement.tail-8}px;transform:rotate(-90deg);`:`top:-28px;left:${p.$placement.tail-8}px;transform:rotate(90deg);`}`;
const Speaker=styled.span`display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:4px;font-size:10px;font-weight:700;color:#866346;`;
const Words=styled.span`display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;overflow-wrap:anywhere;font-size:12px;line-height:17px;`;
export function SpeechBubble({placement,name,text,latest,onOpen,ready,focusOnOpen}:{ready:boolean;focusOnOpen:boolean;placement:BubblePlacement;name:string;text:string;latest:boolean;onOpen:(element:HTMLElement)=>void}){
 const ref=useRef<HTMLButtonElement>(null);
 useEffect(()=>{if(ready&&focusOnOpen)ref.current?.focus({preventScroll:true})},[ready,focusOnOpen,name]);
 return <Bubble ref={ref} hidden={!ready} $placement={placement} data-region="speech-bubble" type="button" aria-label={`${name} 的完整公开发言`} title="点击查看完整发言" onClick={event=>onOpen(event.currentTarget)}><Tail $placement={placement} aria-hidden="true"/><Speaker>{latest?'最新':'回看'} · {name}</Speaker><Words>{text}</Words></Bubble>;
}
