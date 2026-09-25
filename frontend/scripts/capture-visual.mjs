import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, "..", "visual-evidence");
mkdirSync(OUT_DIR, { recursive: true });

async function main() {
  console.log("启动无头 Chromium 进行视觉快照录制...");
  const browser = await chromium.launch({ headless: true });

  // 1. 桌面端 1440x900
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2, // 高清视网膜屏幕
  });
  const page = await context.newPage();

  console.log("导航至 http://localhost:3000/workbench ...");
  await page.goto("http://localhost:3000/workbench", { waitUntil: "networkidle" });
  await page.addStyleTag({ content: "nextjs-portal, [data-nextjs-toast], #__next-build-watcher, [data-nextjs-dialog] { display: none !important; }" });
  await page.waitForTimeout(1000); // 等待 React 渲染与动画平稳

  const desktopPath = resolve(OUT_DIR, "workbench-desktop-1440.png");
  await page.screenshot({ path: desktopPath, fullPage: false });
  console.log(`已捕获桌面端快照: ${desktopPath}`);

  // 1.1 桌面端空白欢迎态快照 (点击新建任务)
  try {
    const newBtn = await page.$("button:has-text('新建任务')");
    if (newBtn) {
      await newBtn.click();
      await page.waitForTimeout(500);
      const welcomePath = resolve(OUT_DIR, "workbench-desktop-welcome-1440.png");
      await page.screenshot({ path: welcomePath, fullPage: false });
      console.log(`已捕获桌面端空白欢迎态快照: ${welcomePath}`);
    }
  } catch (e) {
    console.log("跳过空白欢迎态捕获:", e);
  }

  // 2. 桌面端暗黑模式
  const themeBtn = await page.$("button[title*='切换为深色模式']");
  if (themeBtn) {
    await themeBtn.click();
    await page.waitForTimeout(400);
    const darkPath = resolve(OUT_DIR, "workbench-desktop-dark-1440.png");
    await page.screenshot({ path: darkPath, fullPage: false });
    console.log(`已捕获暗色模式快照: ${darkPath}`);
    // 切换回浅色
    const lightBtn = await page.$("button[title*='切换为浅色模式']");
    if (lightBtn) await lightBtn.click();
  }

  // 3. 移动端 375x812 (iPhone 模式)
  const mobileContext = await browser.newContext({
    viewport: { width: 375, height: 812 },
    deviceScaleFactor: 2,
    isMobile: true,
  });
  const mobilePage = await mobileContext.newPage();
  await mobilePage.goto("http://localhost:3000/workbench", { waitUntil: "networkidle" });
  await mobilePage.addStyleTag({ content: "nextjs-portal, [data-nextjs-toast], #__next-build-watcher, [data-nextjs-dialog] { display: none !important; }" });
  await mobilePage.waitForTimeout(1000);

  const mobileChatPath = resolve(OUT_DIR, "workbench-mobile-chat-375.png");
  await mobilePage.screenshot({ path: mobileChatPath });
  console.log(`已捕获移动端对话快照: ${mobileChatPath}`);

  // 打开移动端侧栏快照
  try {
    const toggleBtn = await mobilePage.$("button[title*='切换侧边栏']");
    if (toggleBtn) {
      await toggleBtn.click();
      await mobilePage.waitForTimeout(400);
      const mobileSidebarPath = resolve(OUT_DIR, "workbench-mobile-sidebar-375.png");
      await mobilePage.screenshot({ path: mobileSidebarPath });
      console.log(`已捕获移动端侧栏快照: ${mobileSidebarPath}`);
    }
  } catch (e) {
    console.log("跳过移动端侧栏快照:", e);
  }

  await browser.close();
  console.log("全部视觉快照捕获完毕！");
}

main().catch((err) => {
  console.error("快照捕获出错:", err);
  process.exit(1);
});
