/**
 * 首屏启动画面（构建期注入，零运行时依赖）。
 *
 * 背景：index.html 的 mount 容器原生为空，umi.js 下载/执行期间用户只能看到纯白屏。
 * 本插件经 umi `modifyHTML` 钩子（cheerio）在构建/开发期向 mount 容器注入纯内联的
 * 「协作组网」启动画面，叙事分两拍：
 *   1) 入场（~1s）：品牌三节点（对应 favicon mark）错峰浮现，链路自顶部协调节点
 *      （蓝）向外生长，网络「成形」；
 *   2) 运转（循环）：品牌色数据包沿链路循环穿行（多 bot 消息流转），协调节点带
 *      呼吸光环，状态字幕与 shimmer 进度条同拍轮播（1.6s/拍），传达「多智能体协作」
 *      这一产品本质。
 * React createRoot 首次提交会清空容器 innerHTML，画面随之整体退场——无 JS 钩子、
 * 无监听器、无 FOUC 处理。交接的硬切由 `#root > *` 淡入规则软化：React 接管时
 * 新插入的容器 direct children 自动命中该选择器，320ms 淡入铺过下方纯白。
 *
 * 兼容要点：
 * - 全部内容内联（无 src/href/字体外链），雨燕 CDN 路径改写与内部 {{prefix}} 模板
 *   替换均不受影响；
 * - mount 容器 id 经 api.config.mountElementId 动态获取（Open='root' /
 *   内部='root-master'），两条组装链共用本插件；交接淡入规则对两个 id 同时声明；
 * - 产品名经 api.config.title 动态获取（Open='Avernet' / 内部='TeamClaw'），
 *   本插件不硬编码产品名；
 * - 亮色为唯一形态：app 主题默认亮色（.dark 仅为 localStorage 显式设置、不跟随
 *   系统），splash 与 app 默认形态保持一致，避免系统偏好派与 app 实际形态错配反闪；
 * - prefers-reduced-motion 下停用全部动画：组网静置呈现、字幕只保留第一拍、
 *   交接即时呈现。
 */
import type { IApi } from 'umi';

/** 骨架屏样式：底色与应用正文 --background（0 0% 100% 纯白）一致，保证交接无闪烁。 */
const SPLASH_STYLE = `
#boot-splash{position:fixed;inset:0;z-index:2147483000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;background:#fff;user-select:none;-webkit-font-smoothing:antialiased;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif}
.bs-stage{width:120px;height:96px}
/* 入场第一拍：节点错峰浮现（协调者先到，两侧随后） */
.bs-node-1{opacity:0;animation:bs-pop .45s ease-out both}
.bs-node-2{opacity:0;animation:bs-pop .45s ease-out .12s both}
.bs-node-3{opacity:0;animation:bs-pop .45s ease-out .24s both}
@keyframes bs-pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
/* 入场第二拍：链路自协调节点向外生长（stroke-dashoffset 60→0 沿 x1→x2 方向画线） */
.bs-link{stroke:#d8dce4;stroke-width:1.5;stroke-linecap:round;fill:none;stroke-dasharray:60;stroke-dashoffset:0;animation:bs-draw .7s ease-out .3s both}
.bs-link-2{animation-delay:.45s}
.bs-link-3{animation-delay:.6s}
@keyframes bs-draw{from{stroke-dashoffset:60}to{stroke-dashoffset:0}}
/* 运转拍：数据包沿链路往复，端点渐隐避免与节点重叠；三条链路错峰出发显自然 */
.bs-pkt-1{opacity:0;animation:bs-go-1 2s ease-in-out 1s infinite}
.bs-pkt-2{opacity:0;animation:bs-go-2 2s ease-in-out 1.45s infinite}
.bs-pkt-3{opacity:0;animation:bs-go-3 2s ease-in-out 1.9s infinite}
@keyframes bs-go-1{0%{opacity:0;transform:translate(0,0)}12%,88%{opacity:1}100%{opacity:0;transform:translate(34px,-44px)}}
@keyframes bs-go-2{0%{opacity:0;transform:translate(0,0)}12%,88%{opacity:1}100%{opacity:0;transform:translate(-34px,-44px)}}
@keyframes bs-go-3{0%{opacity:0;transform:translate(0,0)}12%,88%{opacity:1}100%{opacity:0;transform:translate(68px,0)}}
/* 协调节点（顶部蓝）的呼吸光环，待网络成形后开始 */
.bs-halo{fill:none;stroke:#2563eb;stroke-width:1;opacity:0;transform-box:fill-box;transform-origin:center;animation:bs-halo 2.4s ease-out 1.4s infinite}
@keyframes bs-halo{0%{transform:scale(.5);opacity:.45}70%{opacity:.05}100%{transform:scale(2.1);opacity:0}}
.bs-word{font-size:21px;font-weight:700;letter-spacing:.05em;color:#111113;line-height:1}
/* 状态字幕：4.8s 一轮三拍（1.6s/拍，与 shimmer 同拍）持续循环，拍间错峰滚动 */
.bs-caption{position:relative;width:220px;height:18px;margin-top:2px}
.bs-cap{position:absolute;inset:0;text-align:center;font-size:12px;letter-spacing:.22em;color:#9aa0aa;opacity:0;animation:bs-cap-cycle 4.8s ease-out infinite both}
.bs-cap-2{animation-delay:1.6s}
.bs-cap-3{animation-delay:3.2s}
@keyframes bs-cap-cycle{0%{opacity:0}6%{opacity:1}28%{opacity:1}38%{opacity:0}100%{opacity:0}}
.bs-bar{width:104px;height:5px;border-radius:9999px;background:#f1f2f4;overflow:hidden;position:relative;margin-top:6px}
.bs-bar::after{content:'';position:absolute;inset:0;border-radius:9999px;transform:translateX(-100%);background:linear-gradient(90deg,transparent,rgba(37,99,235,.25),transparent);animation:bs-sweep 1.6s ease-in-out infinite}
@keyframes bs-sweep{55%,100%{transform:translateX(100%)}}
/* 交接软过渡：容器 direct children 淡入——React 接管时新建的应用子树命中该
   选择器，320ms 淡入铺过 splash 留下的纯白；splash 自身入场亦同享此规则。 */
#root>*,#root-master>*{animation:bs-app-in .32s ease-out both}
@keyframes bs-app-in{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){.bs-node-1,.bs-node-2,.bs-node-3{animation:none;opacity:1}.bs-link,.bs-pkt-1,.bs-pkt-2,.bs-pkt-3,.bs-halo,.bs-cap,.bs-bar::after,#root>*,#root-master>*{animation:none}.bs-cap-1{opacity:1}.bs-halo{opacity:.22;transform:scale(1.1)}}
`;

/**
 * 「协作组网」画面：品牌三节点 mark（左绿/顶蓝/右墨，呼应 favicon 笔画）展开为
 * 协作网——节点错峰浮现、链路自协调节点向外生长、数据包沿链路循环穿行；下衬
 * 产品名（经 config.title 注入）与三拍轮播状态字幕、shimmer 进度条。
 */
const splashMarkup = (wordmark: string) => `
<div id="boot-splash" aria-hidden="true">
  <svg class="bs-stage" width="120" height="96" viewBox="0 0 120 96" xmlns="http://www.w3.org/2000/svg" fill="none">
    <line class="bs-link bs-link-1" x1="60" y1="22" x2="26" y2="66"></line>
    <line class="bs-link bs-link-2" x1="60" y1="22" x2="94" y2="66"></line>
    <line class="bs-link bs-link-3" x1="26" y1="66" x2="94" y2="66"></line>
    <circle class="bs-pkt-1" cx="26" cy="66" r="2.6" fill="#10b981"></circle>
    <circle class="bs-pkt-2" cx="94" cy="66" r="2.6" fill="#111113"></circle>
    <circle class="bs-pkt-3" cx="26" cy="66" r="2.6" fill="#2563eb"></circle>
    <circle class="bs-halo" cx="60" cy="22" r="10"></circle>
    <circle class="bs-node-1" cx="60" cy="22" r="6" fill="#2563eb"></circle>
    <circle class="bs-node-2" cx="26" cy="66" r="6" fill="#10b981"></circle>
    <circle class="bs-node-3" cx="94" cy="66" r="6" fill="#111113"></circle>
  </svg>
  ${wordmark ? `<div class="bs-word">${wordmark}</div>` : ''}
  <div class="bs-caption">
    <span class="bs-cap bs-cap-1">正在接入协作网络</span>
    <span class="bs-cap bs-cap-2">正在唤醒智能体</span>
    <span class="bs-cap bs-cap-3">正在准备会话</span>
  </div>
  <div class="bs-bar"></div>
</div>
`;

export default (api: IApi) => {
  api.modifyHTML({
    fn: ($) => {
      // 幂等保护：上游若已注入（如内部 overlay 重复挂本插件）则不动。
      if ($('#boot-splash').length > 0) return $;
      const mountId = api.config.mountElementId || 'root';
      if ($(`#${mountId}`).length === 0) return $;
      // 产品名取两形态各自的 config.title（Open='Avernet' / 内部='TeamClaw'）。
      const wordmark = String(api.config.title || '').trim();
      $('head').append(`<style id="boot-splash-style">${SPLASH_STYLE}</style>`);
      $(`#${mountId}`).append(splashMarkup(wordmark));
      return $;
    },
  });
};
