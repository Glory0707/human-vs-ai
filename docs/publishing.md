# 上架指南（VS Code Marketplace / Obsidian 社区目录）

两个渠道都是零成本分发：账号免费、托管在各自官方目录。前置纪律不变——
版本号改动必须先重建三端（`python tools/build_vscode.py` / `build_obsidian.py`
会把 `human_vs_ai/__init__.py` 的版本同步进 package.json / manifest.json / versions.json），
回归全绿再发布。

## VS Code Marketplace

### 一次性准备

1. 创建发布者：登录 <https://marketplace.visualstudio.com/manage>（Azure DevOps 组织，
   免费），建一个发布者，**id 必须是 `glory0707`**（package.json 已写死）。
2. 生成 PAT：<https://dev.azure.com> → User settings → Personal access tokens →
   Organization 选 **All accessible organizations**，Scopes 勾 **Marketplace → Manage**。
3. `npx @vscode/vsce login glory0707`（粘贴 PAT；凭据存本机 keytar）。

### 每次发布

```bash
python tools/build_vscode.py            # 版本号等源码改动后先重建（版本随主包对齐）
cd vscode-extension
npx @vscode/vsce package --no-dependencies   # 本地自检：产出 human-vs-ai-<v>.vsix
# 手工安装 .vsix 冒烟（VS Code 扩展面板 ⋯ → 从 VSIX 安装）后：
npx @vscode/vsce publish                # 用已入库产物打包发布（默认取 package.json 版本）
```

- 打包内容由 `.vscodeignore` 控制（smoke 测试与杂 md 不入包）；README.md 即
  marketplace 列表页，icon.png（128px 印章）为列表图标。
- 审核通常几分钟到几天；发布后链接
  `https://marketplace.visualstudio.com/items?itemName=glory0707.human-vs-ai`。
- 版本号只能升不能降；与仓库 release 的 tag 保持同一版本串。

## Obsidian 社区目录

### 每次发布（上架前后都一样）

1. GitHub Releases 打 tag（**tag 名 = 插件版本号**，如 `0.31.0`），附件三个文件：
   `main.js`、`manifest.json`、`versions.json`（都出自 `obsidian-plugin/`，构建产物已入库）。
2. `gh release create <版本> obsidian-plugin/main.js obsidian-plugin/manifest.json obsidian-plugin/versions.json`

### 首次上架（一次性）

1. 确认 `obsidian-plugin/manifest.json`：id `human-vs-ai`（2026-10 核查时
   community-plugins.json 15591 行无此 id，可用）、描述 ≤250 字、`isDesktopOnly: true` 如实。
2. Fork <https://github.com/obsidianmd/obsidian-releases>，**只改
   `community-plugins.json`**（改任何别的文件会被拒），在数组末尾追加：

   ```json
   {
     "id": "human-vs-ai",
     "name": "Human vs AI 中文 AI 味分析",
     "author": "Glory0707",
     "repo": "Glory0707/human-vs-ai",
     "description": "可解释的中文 AI 味分析——指出笔记里哪里像模板、为什么、怎么改。风格提示，不是 AI 判定。纯本地运行。"
   }
   ```

3. 发 PR，标题写 `Add human-vs-ai plugin`；自动化审核按开发者政策跑
   （本插件纯本地、零网络调用、无遥测，是政策友好的形态）；通过后人工复核合并。
4. 合并后 Obsidian 应用端拉取 release 资产，用户即可在社区插件市场搜索安装。

### 政策红线（每次大改自查）

- 不做网络请求、不收集遥测（引擎本身就是零网络设计，别在插件层加）；
- `versions.json` 每次发版都要含新版本 → minAppVersion 映射（构建脚本自动维护）；
- description 与 manifest 如实一致，不堆关键词。
