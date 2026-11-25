# cips-input

把剪贴板里的文本「打」进禁止粘贴的输入框。

cips-input 是一个基于 [PIME](https://github.com/EasyIME/PIME) 的 Windows 输入法后端：不改动上游 C++ 代码，只在上游的 Python 后端目录里放一个新的输入法模块。它面向的场景是那些**粘贴无效、但打字有效**的输入框——禁用 `onpaste` 的网页编辑器、校验 `event.isTrusted` 的表单、把 `maxlength` 设成 1 的六格验证码、只认真实 `keydown` 的自研控件。

```
复制文本  →  聚焦输入框  →  Ctrl+Alt+V
```

## 工作原理

普通粘贴走的是 `paste` → `beforeinput(inputType = "insertFromPaste")`。页面有至少三种办法拦掉它：`onpaste="return false"`、`preventDefault()`、或者用 `event.isTrusted` 挡掉合成事件。

本项目的做法是**逐字符注入真实按键**：`SendInput` + `KEYEVENTF_UNICODE`，每个字符一对 `keydown`/`keyup`。目标程序看到的是货真价实的键盘输入——不经过剪贴板，不触发 `paste`，`isTrusted` 恒为 `true`，也不存在 `inputType` 白名单。

> 浏览器 JS 拿不到 `LLKHF_INJECTED` 标志，所以在纯网页环境里，注入的按键和物理键盘在事件层面没有区别。

### 为什么没有整串提交

TSF 的 `setCommitString` 能做到一次上屏整段文本，但它**必须在一个编辑会话里**调用。PIME 只在 `onKeyDown` 回调里提供编辑会话，而热键走的是保留键（preserved key）与 `filterKeyDown`，这两条路径都没有会话——`setCommitString` 会被框架静默丢弃（`PIMETextService\PIMEClient.cpp:332` 的 `if (session != nullptr)`）。

试过用「注入一个 F24 按键把会话引出来」的桥接方案：注入的 F24 根本不会被 TSF 路由到输入法，桥接永远是空转，所以在 v0.2 里删掉了，v0.3 又删掉了整套遗留的整串提交代码。现在只剩逐字注入一条通道，靠**策略与档位**适配不同目标。

### 策略与档位

「用哪种节奏」由 `profiles.json` 和热键共同决定：`Ctrl+Alt+V` 按前台程序匹配 profile 取策略，其余热键强制一种策略。

| 热键 | 节奏 | 适合 |
|---|---|---|
| `Ctrl+Alt+V` | 由当前程序的 `profile.mode` 决定；默认 `human_normal`（拟人·标准速 170 ± 90 ms） | 日常默认 |
| `Ctrl+Alt+1` | 拟人节奏·慢速（255 ± 135 ms） | 对时序敏感、容易丢字的程序 |
| `Ctrl+Alt+2` | 固定 75 ms/字 | 快速：能承受快注入的浏览器 |
| `Ctrl+Alt+3` | 逐字符 + `per_box_wait_ms` | 六格验证码这类「一格一字符」的组件 |
| `Ctrl+Alt+Z` | 完全复用上一次的策略与档位 | 剪贴板被覆盖后重发 |

随包发布的 `profiles.json` 里，默认策略是拟人·标准速，记事本/Office 被配成固定 75 ms/字（这两个目标的文本栈在高频按键下更容易丢字，固定档也更快）。想统一成拟人节奏，把对应 profile 的 `mode` 改成 `human_normal` 即可。

拟人节奏下，`Ctrl+Alt+V`（标准速）一段 45 字约 8–12 秒，`Ctrl+Alt+1`（慢速）约 12–17 秒；固定档 45 字约 3.4 秒。

### 拟人节奏

机械等间隔的按键不自然，所以这两档不是固定延时，而是按「人打字」的节奏生成每一拍：

- **随机间隔**：每个字符在 `base_delay_ms` 附近抖动 `±jitter_ms`，所以节奏永远不会重复。
- **空格停顿**：空格后额外停 `space_pause_ms`，模拟换词。
- **换行停顿**：换行后额外停 `newline_pause_ms`，模拟换行/换段。
- **标点停顿**：`. , ; : ! ?` 与中文标点后额外停 `punctuation_pause_ms`。
- **犹豫**：每个字符有 `hesitation_chance` 的概率额外加 `hesitation_min_ms`–`hesitation_max_ms` 的停顿，模拟思考。

两档各自一块参数，放在 `profiles.json` 顶层，改完即生效（配置有 2 秒节流，最多 2 秒后重新读取）：

```json
"human": {                 // Ctrl+Alt+1（也可被 mode: "human" 的 profile 选用）
  "base_delay_ms": 255,
  "jitter_ms": 135,
  "space_pause_ms": 210,
  "newline_pause_ms": 570,
  "punctuation_pause_ms": 180,
  "hesitation_chance": 0.03,
  "hesitation_min_ms": 450,
  "hesitation_max_ms": 1350
},
"human_normal": {          // Ctrl+Alt+V 的默认档（mode: "human_normal"）
  "base_delay_ms": 170,
  "jitter_ms": 90,
  "space_pause_ms": 140,
  "newline_pause_ms": 380,
  "punctuation_pause_ms": 120,
  "hesitation_chance": 0.03,
  "hesitation_min_ms": 300,
  "hesitation_max_ms": 900
}
```

写错、漏写、写成字符串都不影响使用，坏值会被忽略并退回默认值。这两块参数是**全局**的；按程序区分的是 `profile.mode`（拟人 / 固定 / 逐格），不是节奏参数。

### 中途打断

注入是在后台线程里逐字进行的，三条中止路径：

- **按 `Esc`**：立刻停，`Esc` 只在注入期间被输入法接管，平时照常给应用。
- **再按一次同一个热键**：重新开始一次注入（旧的自然被取消）。
- **切走焦点窗口**：注入会立刻放弃，日志记 `typing aborted at i/N: focus left the target window`。这一条是为了防止文字被打进别的窗口。

## 安装

### 方式 A：一键安装包（推荐）

用 Windows 自带的 .NET Framework 编译器把整个安装包打成**单个 exe**：

```powershell
cd D:\Project\cips\cips-input
powershell -ExecutionPolicy Bypass -File deploy\bundle\build_installer.ps1
```

产物是 `build\dist\cips-input-setup-<version>.exe`（内置 PIME 与预编译白霜词库，另附 `SHA256SUMS.txt`）。第一次构建会下载官方 PIME 安装包、`librime` 1.17.0（x86）与 `rime-frost` 1.0.6 到 `build\cache` 并校验 SHA-256，之后可离线重复构建；`-PimeSetup <路径>` 可指定本地安装包，`-SkipDownload` 强制离线，`-SkipSelfTest` 跳过自检。构建需要 .NET Framework 4.x（Windows 10/11 自带）与 PowerShell 5.1，不需要 NSIS、7-Zip 或 PyInstaller。

双击安装包 → 接受 UAC → 可按需勾选「附带安装万象语言模型 (`wanxiang-lts-zh-hans.gram`，约 373 MB)」→ 点击「开始安装」→ `Win + Space` 选「cips-input」。安装器的行为：

- **安装器与大模型分离**：373 MB 的万象语言模型默认不打进 exe，而在安装时作为可选项：
  - 若安装包同目录存在 `wanxiang-lts-zh-hans.gram`，界面会自动勾选并离线安装；
  - 也可在安装界面点击「选择本地模型...」，或勾选后由安装器在线下载并校验 SHA-256；
  - 静默/自动化模式：`/batch -InstallGram`（在线下载）或 `/batch -InstallGram -GramPath <路径>`（本地文件）。
- **机器上没有 PIME**：静默运行包内**未改动**的官方 PIME 2.0.0-beta4 安装包，并默认移除 PIME 自带的繁体新酷音输入法，只保留 `cips-input`（内置白霜拼音）。
- **机器上已经有 PIME**：只添加/更新 cips-input 模块，不动其它输入法（安装标记记为 `module` 模式，卸载时也只移除模块）。
- 固定安装到 `C:\Program Files (x86)\PIME`：PIME 的 C++ 客户端把该路径硬编码在 `PIMEImeModule.cpp`（`SHGetFolderPathW(CSIDL_PROGRAM_FILESX86) + "\PIME"`），装到别处语言配置注册不上，所以**必须管理员权限，做不了便携版**。
- 自动注册 x64/x86 文本服务（ARM64 机器注册 arm64 转发 DLL）、重启 `PIMELauncher`、刷新 `ctfmon`，并在「应用和功能」和开始菜单里注册卸载入口。
- 卸载：应用和功能里的 “cips-input”，或开始菜单里的 “Uninstall cips-input”。`full` 模式会调用 PIME 自己的卸载器；`module` 模式只移除模块并重新注册/重启服务。
- 安装日志：`%TEMP%\cips-input-install.log`；静默/自动化：`cips-input-setup-<版本>.exe /batch -DryRun` 只检查不改动，其余参数原样传给 `install.ps1`。
- 许可证与第三方声明随包安装到 `%ProgramData%\cips-input`（`THIRD_PARTY_NOTICES.md` + `licenses/`）。卸载后这些文件一并移除；安装包缓存在 `build\`，已被 `.gitignore` 忽略。

### 方式 B：手动部署（开发/调试）

已经装好 PIME 时，也可以只部署模块、不打包：

**1. 安装 PIME 2.0.0-beta4**

下载官方安装包（48,664,288 字节，SHA-256 `3bc146bd89daa6b5eb89d2346ece445f8752e0df6d194959644e359ea197fcdf`）：

<https://github.com/EasyIME/PIME/releases/download/v2.0.0-beta4/PIME-2.0.0-beta4-setup.exe>

装完先确认 PIME 自带的输入法能用，再继续。

**2. 部署本模块**（管理员 PowerShell）

```powershell
cd D:\Project\cips\cips-input
.\deploy\install_backend.ps1 -PrepareRime
```

脚本会做四件事：探测 PIME 安装目录（读 `backends.json` 校验，不靠猜注册表）、按需拉取并预编译白霜拼音（`-PrepareRime`，可选加 `-InstallGram` 挂载万象 `.gram` 模型）、把 `src\input_methods\cips` 复制到 `<PIME>\python\input_methods\cips`、用 `regsvr32` 重新注册 x64 与 x86 的 `PIMETextService.dll`、重启 `PIMELauncher.exe`。

常用参数：

```powershell
.\deploy\install_backend.ps1 -SkipRegister                       # 只更新 Python 代码（保留已部署的 Rime 数据）
.\deploy\install_backend.ps1 -PrepareRime -InstallGram           # 一并部署白霜拼音 + 万象 gram 模型
.\deploy\install_backend.ps1 -PrepareRime -GramPath D:\wanxiang-lts-zh-hans.gram
.\deploy\install_backend.ps1 -SkipRestart                        # 不改动正在运行的后端
.\deploy\install_backend.ps1 -Uninstall                          # 移除模块
.\deploy\install_backend.ps1 -PimeDir "D:\PIME"                  # 指定安装目录
```

**改 Python 代码只需要 `-SkipRegister`**：后端进程每次加载输入法时重新 import，热键逻辑不涉及 TSF 注册表。只有改了 `ime.json`（名字、GUID、图标、语言）才需要重新注册。

**3. 选中 cips-input**

`Win + Space` 或任务栏输入法列表里选「cips-input」，图标是蓝底白字 **IPT**。

**改完名字/图标后显示不对？** TSF 会把语言配置的描述和图标缓存到当前会话，重新注册也不刷新。注销重新登录即可；急用可以 `Stop-Process -Name ctfmon -Force`（Windows 会自动重启它）。另外 ICO 不能用纯 PNG 帧——`ExtractIconEx` 只认 BMP 帧，否则会退回默认的语言字形，`tools\build_icon.py` 已按 BMP（≤128）+ PNG（256）混合写入。

## 用法

| 热键 | 作用 |
|---|---|
| `Ctrl + Alt + V` | 按前台程序匹配 `profiles.json` 的策略注入 |
| `Ctrl + Alt + 1` | 慢速拟人注入（255 ± 135 ms 起跳，带停顿） |
| `Ctrl + Alt + 2` | 快速固定 75 ms/字 |
| `Ctrl + Alt + 3` | 逐格注入（otp，六格验证码用） |
| `Ctrl + Alt + Z` | 按上一次完全相同的策略与档位重发 |

再按一次相同热键会取消正在进行的逐字注入。

日常中文打字由内置的**白霜拼音（Rime Frost）**通过标准 TSF 编辑会话上屏（支持全拼、自然码/小鹤/微软/搜狗双拼、用户词典记忆，以及可选的万象语言模型 `wanxiang-lts-zh-hans.gram`）；默认采用**横条配列**与**白霜圆角**皮肤，任务栏语言栏菜单可一键切换 **Gemini 星辉猫娘** / **白霜圆角** / **系统原生**皮肤、横竖配列、是否加载万象模型、中英文、全半角、简繁、Emoji、拆分提示与输入方案。用户词库位于 `%APPDATA%\cips-input\rime`，界面设置位于 `%APPDATA%\cips-input\settings.json`。

热键是通过 TSF 保留键注册的（`addPreservedKey`），因此不怕被应用的快捷键表吞掉——这是 `Ctrl+Alt+V` 在多数程序里能生效的原因。

**提示气泡不一定出现。** 热键大多经由保留键回调到达，而那条回调没有 TSF 编辑会话；此时让 PIME 弹 `showMessage` 会让宿主程序崩溃（`PIMEClient.cpp` 在判空编辑会话之前就解引用它）。所以「剪贴板没有文本」「注入被 UIPI 拦截」这类提示**只写 `cips.log`**，不弹气泡。日志才是主要诊断入口。

## 配置 `profiles.json`

部署后位于 `<PIME>\python\input_methods\cips\profiles.json`。改完直接生效——后端每 2 秒最多检查一次文件 mtime，不需要重启。注意 `Ctrl+Alt+Z` 重发的是「上一次实际执行的计划」，改配置只影响之后的新注入。

随包发布的就是下面这份（`human` / `human_normal` 两块见上一节）：

```json
{
  "default": {
    "name": "default",
    "mode": "human_normal",
    "char_delay_ms": 60,
    "chunk_size": 64,
    "chunk_wait_ms": 30,
    "per_box_wait_ms": 60,
    "trailing_tab": false
  },
  "profiles": [
    {
      "name": "editors",
      "match_exe": ["notepad.exe", "notepad++.exe", "code.exe", "sublime_text.exe"],
      "mode": "char",
      "char_delay_ms": 75
    },
    {
      "name": "office",
      "match_exe": ["winword.exe", "excel.exe", "powerpnt.exe", "wps.exe"],
      "mode": "char",
      "char_delay_ms": 75
    }
  ]
}
```

常见改法：

```json
// 让 Chrome 走慢速拟人
{"name": "chrome", "match_exe": ["chrome.exe", "msedge.exe"], "mode": "human"}

// 六格验证码按窗口类自动走逐格模式
{"name": "otp-boxes", "match_class": ["OTPBox"], "mode": "otp",
 "per_box_wait_ms": 80, "trailing_tab": true}

// 某个老系统固定 120 ms/字
{"name": "legacy", "match_exe": ["legacy.exe"], "mode": "char", "char_delay_ms": 120}
```

| 字段 | 含义 |
|---|---|
| `mode` | `Ctrl+Alt+V` 的策略：`human_normal`（拟人·标准速，默认）/ `human`（拟人·慢速）/ `char`（固定间隔）/ `segmented`（分批）/ `otp`（逐格）。写错会退回 `human_normal`。 |
| `match_exe` | 进程名列表，支持 `*` 通配，大小写不敏感 |
| `match_class` | 窗口类名列表（如 `Chrome_WidgetWin_1`） |
| `char_delay_ms` | `mode` 为 `char` / `segmented` 时的字符间隔（`segmented` 还用它控制段内间隔） |
| `chunk_size` / `chunk_wait_ms` | `segmented` 模式的分批大小与批间停顿 |
| `per_box_wait_ms` | `otp` 模式下每格之间的停顿 |
| `trailing_tab` | `otp` 模式发完后是否补一个 Tab（跳到下一个输入框） |

`mode` 只影响 `Ctrl+Alt+V`：`Ctrl+Alt+1/2/3` 强制各自策略，`Ctrl+Alt+Z` 复用上一次的计划（包括模式、档位和文本）。profile 里的 `char_delay_ms` 等字段只在对应模式下有意义，其它模式会忽略。

匹配优先级：`match_exe` 与 `match_class` 都写 > 只写 `match_exe` > 只写 `match_class` > `default`。**`match_exe` 和 `match_class` 同时写是「且」的关系**，两个都得中；除非确实需要，只写 `match_exe`。

配置解析是全容错的：文件坏了、字段类型不对、数值为负，都会安静地退回 `default` / 夹紧到合法范围。想确认有没有生效，看 `cips.log` 里的 `profiles loaded: N, default=...` 与每次激活的 `active profile: ...`。

## 排错

**日志有两个，别搞混**

```
%LOCALAPPDATA%\PIME\Logs\cips.log              诊断日志（本项目写的）
%LOCALAPPDATA%\PIME\Logs\python_backend.log    只有未捕获异常才会写
```

PIME 只把符合 `PIME_MSG|` 协议的行当作回复，其余 stdout 一律丢弃——所以后端的 `print()` **不会**出现在任何日志里。因此本项目自己写 `cips.log`，记录：输入法被激活/停用（含是哪个程序激活的、命中哪个 profile）、保留键请求、热键是否触发、按什么策略注入多少字、预计耗时、注入失败原因。正常打字不写日志，不会拖慢按键。

**后端是否活着**

```powershell
& "$env:ProgramFiles\PIME\PIMELauncher.exe" /console
```

前台运行能看到后端启动和 IPC 往返。正常退出用 `/quit`。

**热键没反应**

1. `Win + Space` 确认当前输入法确实是 cips-input——注意 Windows 是按窗口记忆输入法的：**新开一个窗口不一定就是你刚切换的那个输入法**，要在这个窗口里再切一次。
2. 看 `cips.log`：
   - 文件不存在 → 这个输入法根本没被激活（多半是窗口里的输入法不是 cips-input），不是代码问题。
   - 有 `activate` 但没有 `preserved keys requested: 5` → 后端没走到注册那一步，可能加载失败。注意这一行**每个宿主程序只写一次**（PIME 给每个应用进程建一个输入法实例），log 里只有别的程序留下的旧行是正常的。
   - 有 `activate` 和 `preserved keys requested: 5`，但按热键后没有 `preserved key <guid> -> auto` → TSF 没把组合键路由过来；保留键请求本身没有返回值，这行日志只证明请求发出过，真正可观测的是 `onPreservedKey` 打出的那一行。
   - 有 `preserved key <guid> -> auto` 但没有 `dispatch` → 没有可粘贴的文本（剪贴板空或不是文本）。
   - 有 `dispatch ... chars=N` 但屏幕上没字 → 目标控件不吃注入按键，换 `Ctrl+Alt+1` 再试。
3. 目标窗口是管理员权限时，注入会被 UIPI 拦掉，日志会记一条 `注入被拦截` 消息。

**粘贴能过，但打完字页面没反应**

说明这一档太快，目标程序把中间按键丢了。换 `Ctrl+Alt+1`（拟人节奏，默认 255 ± 135 ms 起跳、带停顿）；仍不行就在 `profiles.json` 里把 `human.base_delay_ms` 调大，或给该程序写一个 `mode: "char"` 且 `char_delay_ms` 更大的 profile。

**逐字注入丢字、乱码、顺序错乱**

这是目标程序文本栈的时序问题，不是注入失败。调大 `char_delay_ms`（默认 60，记事本/Office 类目标用 75）。

**注入很慢 / 想中途放弃**

日志里 `typing: ... estimated=...s` 是这次注入的预计总时长；超过 120 秒会再加一条 warning。2 万字的拟人注入可能长达一小时——按 `Esc` 或切走焦点窗口即可中止。

## 目录结构

```
src/input_methods/cips/      部署单元，整个目录拷到 <PIME>\python\input_methods\
├── ime.json                 输入法元数据（名字 cips-input、GUID、图标、版本）
├── icon.ico                 输入法图标（蓝底白字 IPT）
├── icons/                   语言栏状态图标（中/英、全/半角、设置）
├── profiles.json            每程序注入策略 + 拟人节奏参数
├── cips_ime.py              TextService 实现：Rime Frost 中文输入 + 热键注入线程
├── core/                    纯逻辑层：判空、归一化、分块规划、配置解析、策略解析（不 import win32）
├── rime_engine/             librime 1.17.x ctypes 绑定、键位映射、异步生命周期与菜单样式
├── rime_config/             cips.yaml、默认白霜方案列表与万象 gram 补丁模板
└── win32/                   平台层：剪贴板、SendInput、前台窗口、读回校验
tests/                       单元测试与 Rime 集成测试（纯标准库）
deploy/prepare_rime.ps1      拉取校验 librime 1.17.0 (x86) 与 rime-frost 1.0.6 并预编译词库
deploy/install_backend.ps1   手动部署与注册脚本（方式 B）
deploy/bundle/               一键安装包构建
├── build_installer.ps1      组装 PIME + 预编译白霜词库、编译引导器、自检
├── install.ps1              安装逻辑（full / module 模式 + 可选万象 gram 模型）
├── uninstall.ps1            卸载逻辑（按安装标记分模式处理）
├── common.ps1               两个脚本共用的路径/注册/服务控制/快捷方式工具
├── THIRD_PARTY_NOTICES.md   随包安装的第三方声明
└── installer/               自解压引导器（C# + requireAdministrator 清单 + 可选模型勾选）
tools/fi_probe.py            独立探针：验证某个输入框到底吃不吃注入（--read-back 可读回）
tools/build_icon.py          生成 icon.ico（BMP 帧 + 256 PNG 帧）
docs/pime-api.md             PIME 后端 API 调研（带源码行号证据）
CHANGELOG.md                 版本变更记录
```

## 已知限制

- **逐字注入的间隔不能太短。** 实测：Windows 11 记事本在 20 ms 间隔下会丢字（`123` 变成 `23`、emoji 变乱码），50 ms 才稳定。同一份代码向自建窗口按 `WM_CHAR` 计数验证，5 ms 也不丢——丢字发生在目标程序自己的文本栈里，不是注入层丢事件。默认固定档按 75 ms 设定，想更快请自行调小并接受风险。
- **UIPI**：`SendInput` 不能往权限更高的窗口注入（任务管理器、以管理员运行的程序）。本项目没有绕开这条限制的通道。
- **一键安装包需要管理员权限**，且只能装到 PIME 硬编码的 `C:\Program Files (x86)\PIME`；完整模式下会连同 PIME 自带的其它输入法一起装上，卸载时也会一并移除（已有 PIME 的机器只加模块，不受影响）。
- **提示气泡不一定出现**：热键多半经保留键回调到达，那条路径没有编辑会话，提示只写 `cips.log`，不弹气泡。
- **它不「破解」验证码**。cips-input 只是把**你已经知道的**文本填进去；识别验证码是另一件事。
- **不是所有输入框都能过**。控件如果既拦粘贴、又要求 `keydown`，而目标窗口权限更高，那就走不通。
- **不做针对性规避**。本项目不针对具体平台的检测逻辑做适配或反检测。
- **内置中文输入首次运行需数秒预热**：Rime 在后台线程完成首次用户目录部署，不阻塞 PIME；部署期间按字母键会提示正在加载，稍候片刻即可正常中文输入（热键不受影响）。
- 逐字注入是异步的：热键按下后立刻返回，实际按键在后台线程发出。注入期间再次按热键会取消。
- 单次文本上限 20000 字；拟人档下这可能需要一小时以上，日志会给出预计时长。

## 许可

本仓库的 cips-input 模块沿用上游 PIME 的 **LGPL-2.1-or-later**，完整许可证文本见 [`LICENSE`](LICENSE)。`_pime/` 目录是上游源码的只读参考副本，未入版本库；安装包内置的第三方组件声明见 [`deploy/bundle/THIRD_PARTY_NOTICES.md`](deploy/bundle/THIRD_PARTY_NOTICES.md)。
