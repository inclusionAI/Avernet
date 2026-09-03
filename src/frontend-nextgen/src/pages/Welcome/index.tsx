import { history } from '@umijs/max';
import { HeroSection } from './components/HeroSection';
import { ScenariosSection } from './components/ScenariosSection';
import { WelcomeFooter } from './components/WelcomeFooter';
import { WelcomeHeader } from './components/WelcomeHeader';

/**
 * Open 形态对外欢迎页(产品门面,默认入口 `/`)。独立落地页:AppLayout 外,不进工作台 Shell/侧栏;
 * internal overlay 声明 `/` 与 `/welcome` 覆盖项取消本页路由(config/routes.internal.ts),
 * 仅 Open 形态可达,本仓本地 dev(internal 形态)不可见。
 * 范围:Header-lite + Hero + 场景展示(id="scenarios",移植 Avernet 落地页,素材 src/assets/Images/scenarios)
 * + Footer;接入方式(id="access")仍按需待补,依赖本地引擎接入故事。
 * 顶部品牌蓝渐变场:页面 luminosity 纵深,非新增配色(from-primary/5 走 token)。
 */
export default function Welcome() {
  return (
    // 根节点即滚动容器:全局 body overflow:hidden(global.css),页面需自建 overflow-y-auto
    // (对齐 Avernet BcnHome 模式);h-full 沿 #root 100% 高度链,短内容时 footer 仍钉在底部。
    <div className="flex h-full w-full flex-col overflow-y-auto scroll-smooth bg-gradient-to-b from-primary/5 via-background to-background text-foreground">
      <WelcomeHeader />
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-8 pb-20 pt-12">
        {/* 待补挂载位:接入方式 <section id="access"> */}
        <HeroSection onEnter={() => history.push('/workspace')} />
        <div className="mt-20">
          <ScenariosSection />
        </div>
      </main>
      <WelcomeFooter />
    </div>
  );
}
