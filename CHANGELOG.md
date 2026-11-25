# Changelog

版本号与 `src/input_methods/cips/ime.json` 的 `version` 保持一致。

## 0.5.0

这一版为 `cips-input` 内置了完整的 **白霜拼音（Rime Frost）** 中文输入能力，并将 **万象 LTS 语言模型（`wanxiang-lts-zh-hans.gram`）** 做成安装时的可选项。

### 新增

- 内置 `librime` 1.17.0（32 位 x86，含 `lua` 与 `octagram` 插件）及预编译的 `rime-frost` 1.0.6 词库/方案（全拼与自然码/小鹤/微软/搜狗双拼），日常中文打字走标准 TSF 组合与上屏，`Ctrl+Alt+V/1/2/3/Z` 剪贴板逐字注入热键优先级保持不变。
- 默认改为**横条配列**，新增基于 GDI+ 分层窗口的自定义候选栏皮肤：`白霜圆角`（默认）与 `Gemini 星辉猫娘`（含可点击选词与 `系统原生` 切换），设置保存在 `%APPDATA%\cips-input\settings.json`。
- `RimeEngine`（`src/input_methods/cips/rime_engine/`）：在后台线程异步初始化与部署 Rime，避免阻塞 PIME 的 2 秒 RPC 超时；默认保留用户词典（`enable_user_dict`），并支持在菜单里随时开关「是否加载万象语言模型」。
- 安装器与模型分离：单文件安装包内含 PIME 运行时与预编译白霜词库，在完整安装模式下自动移除 PIME 自带的繁体新酷音（`chewing`）只保留白霜；373 MB 的 `wanxiang-lts-zh-hans.gram` 作为安装时可选项（支持同目录自动识别、手动选文件或在线校验下载），`/batch` 支持 `-InstallGram` 与 `-GramPath`。
- 新增语言栏菜单（中英、全半角、简繁、Emoji、拆分提示、方案切换、皮肤主题、候选栏配列、万象模型开关、异步重新部署、打开用户词库/日志目录）。
- 新增 `deploy/prepare_rime.ps1`：自动缓存校验 `librime` 与 `rime-frost` 压缩包并调用 `rime_deployer.exe --build` 预编译词库。

## 0.4.0

这一版把部署从「先装 PIME、再跑脚本」变成「双击一个 exe」。

### 新增

- 一键安装包：`deploy/bundle/build_installer.ps1` 生成单个自包含
  `cips-input-setup-<version>.exe`（约 47 MB）。包内是官方 PIME
  2.0.0-beta4 安装包（未改动，构建时校验 SHA-256）、cips-input 模块、
  安装/卸载脚本和许可证。
  - 机器上没有 PIME 时：静默运行官方安装包装好 PIME，再把 cips-input 模块
    部署进去；
  - 已经装有 PIME 时：只添加/更新 cips-input 模块，不动其它输入法；
  - 固定安装到 `C:\Program Files (x86)\PIME`（PIME 的 C++ 客户端把该路径
    硬编码在 `PIMEImeModule.cpp`，换目录会导致语言配置注册不上），因此需要
    管理员权限，无法做成便携版。
- 安装器引导程序（`deploy/bundle/installer/Installer.cs`）：用 Windows 自带的
  .NET Framework `csc.exe` 编译，带 `requireAdministrator` 清单（UAC 由它
  自己弹），图形界面显示进度与 PowerShell 日志；`/batch` 模式供 CI 使用。
  构建不依赖 NSIS、7-Zip、PyInstaller 等外部工具。
- 自带卸载：在「应用和功能」里注册 `cips-input` 条目，开始菜单提供卸载与
  启动输入法服务快捷方式；`%ProgramData%\cips-input` 保存安装标记、卸载脚本、
  许可证与第三方声明，PIME 目录保持干净。
- 安装前检查 PIME 目录是否有待重启的删除/重命名操作：官方 PIME 安装器在
  静默模式下会把「待重启」提示按「是」处理并自动重启机器，因此检测到就拒绝
  安装并提示先重启。
- 安装标记记录模式（`full` = 本包连 PIME 一起装、`module` = 只加了模块），
  卸载时按模式处理：`full` 调用 PIME 自己的卸载器，`module` 只移除模块并重新
  注册/重启服务。
- 构建自检：脚本语法解析、`-DryRun`、模块模式在假 PIME 目录上的安装/卸载
  布局测试、引导器 `/batch -DryRun`。自检不过不产出安装包。

### 变更

- README 的安装章节改为一键安装包优先，`deploy\install_backend.ps1` 保留为
  开发/调试用的手动部署路径。

## 0.3.0

这一版只做「让代码、配置、文档三者说同一件事」，没有新增注入通道。

### 修复

- `Ctrl+Alt+V` 现在真的按 `profiles.json` 匹配前台程序决定策略。此前该热键恒用
  `human_normal`，per-app 配置实际只对 `Ctrl+Alt+Z` 生效，但 README 却声称它会
  按程序选档。
- `profiles.json` 的 `mode` 现在接受 `human_normal` / `human` / `char` /
  `segmented` / `otp` 五种取值。此前只认已废弃的 `bulk`/`char`/`segmented`，
  写 `otp` 会被静默丢弃并退回 `bulk`。
- `Ctrl+Alt+Z` 改为按上一次的实际策略重发（文本 + 模式 + 档位一起复用），不再
  重新按 profile 猜一次；此前重发可能从拟人节奏变成固定 60 ms。
- `preserved keys` 日志改为诚实的一次性记录：`addPreservedKey()` 没有返回值，
  旧日志无条件打印 `preserved keys registered: 5`，却让 README 拿它当注册失败的
  判据。现在可观测的判据是 `onPreservedKey` 打出的 `preserved key <guid> ->`。
- 保留键只在每个宿主进程（PIME 为每个应用进程建一个输入法实例）第一次激活时
  注册一次，不再每次切换窗口都重复注册。
- 剪贴板区分「没有文本」与「只有空文本」，两种情况的诊断不再混为一谈。
- 注入前按计划时长写一条 `estimated=…s`；超过 120 秒再补一条警告，避免用户
  对 2 万字拟人注入的真实耗时毫无预期。
- profile 的数值字段在解析时夹紧（`char_delay_ms`/`chunk_wait_ms`/
  `per_box_wait_ms` ≥ 0，`chunk_size` ≥ 1），手改配置写负数不会再传出负延时或
  零宽分段。
- 修正 README 中过期的热键延时（200/50 ms）、默认值（40–50 ms）与部署脚本里的
  输入法名称（CIPS → cips-input）。

### 清理

- 删除 `bulk` 模式及 `COMMIT_MODES`：整串提交在 v0.2 已不可用，保留它只会让配置
  和文档对不上。分块规划现在只覆盖 `char` / `segmented`。
- 删除从未被调用的 `sendinput.press_vk`（F24 桥接时代的残留）与
  `clipboard.write_clipboard_text`；`win32/verify.py` 的 `read_window_text`
  接入 `tools/fi_probe.py --read-back`，不再是死代码。
- 删除 `cips_ime` 中恒为假的 `showMessage` 输出路径说明、把 `_report` 的
  `print()` 去掉（PIME 只消费 `PIME_MSG|` 行，stdout 不是日志）。
- `cips_ime` 模块文档改写：不再描述已删除的 `setCommitString` 通道。

### 新增

- `core/strategy.py`：把「热键动作 + profile → 注入计划」抽成纯函数，可单测。
- `tests/test_strategy.py`、`tests/test_shipped_config.py`：覆盖策略解析，并校验
  随包发布的 `profiles.json` / `ime.json` 始终自洽。
- `--read-back` 探针开关。

## 更早版本

- **0.2.0**：删掉 `Ctrl+Alt+V` 的整串提交与 F24 桥接；改为拟人节奏、冻结保留键、
  注入线程化、加 `cips.log`。
- **0.1.0**：首个可用版本：剪贴板 → `SendInput` 逐字注入。
