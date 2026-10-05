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
  marketplace 列表页，icon.png（128px 印章）为列表图标，LICENSE 随扩展目录分发
  （vsce 要求包内含 LICENSE，否则警告）。
- 审核通常几分钟到几天；发布后链接
  `https://marketplace.visualstudio.com/items?itemName=glory0707.human-vs-ai`。
- 版本号只能升不能降；与仓库 release 的 tag 保持同一版本串。

## Obsidian 社区目录

### 提交流程（2026-05-12 起换新系统）

Obsidian 用新站点 **Obsidian Community** 取代了旧的 GitHub PR 流程
（官方公告 *The Future of Obsidian Plugins*，2026-05-12；obsidian-releases
README 同月移除提交流程说明，旧 PR 提交的项目已自动迁移）：

1. 先完成下面的"每次发布"——**GitHub release 仍是分发通道**：打 tag
   （tag 名 = 插件版本号，如 `0.31.0`），附 `main.js`、`manifest.json`、
   `versions.json` 三个文件（都出自 `obsidian-plugin/`，构建产物已入库）：

   ```bash
   gh release create <版本> obsidian-plugin/main.js obsidian-plugin/manifest.json obsidian-plugin/versions.json
   ```

2. 打开 <https://community.obsidian.md/>，GitHub 账号登录开发者面板
   （developer dashboard），连接 GitHub、选择 `Glory0707/human-vs-ai` 仓库提交。
3. 自动审查扫描**每个版本**的安全与代码质量（不再只审首次提交），可看详细
   的建议/警告/失败标记；过审后 24 小时内在应用内可搜索安装。
4. 新目录不接受新的闭源插件——本仓库 MIT，满足。

### manifest 要求（不变）

- id `human-vs-ai`（2026-10 核查 community-plugins.json 15591 行无此 id，可用）；
- 描述 ≤250 字、`isDesktopOnly: true` 如实；
- `versions.json` 每次发版都要含新版本 → minAppVersion 映射（构建脚本自动维护）。

### 政策红线（每次大改自查）

- 不做网络请求、不收集遥测（引擎本身就是零网络设计，别在插件层加）；
- description 与 manifest 如实一致，不堆关键词。
