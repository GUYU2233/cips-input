# PIME 2.0.0-beta4 集成契约调研（供 cips 自定义 Python 后端使用）

- 上游代码：`_pime/`（EasyIME/PIME，`version.txt` = `2.0.0-beta4`，HEAD = `739ed17`）
- 取证方式：① 阅读 C++/Rust/Python 源码；② 在本机用官方内置解释器 `_pime/python/python3/python.exe` 实跑上游 `server.py` 做 IPC 时延实测（实验副本放在 `%TEMP%\pime_ipc_test\`，未改动 `_pime/`）。
- 约定：每条结论后附 `路径:行号`；无法从源码确认的标「未确认」。
- 注意：`_pime/libIME2/` 是 git submodule，本地为空目录。本文涉及的 libIME2 结论取自该 submodule 钉住的 commit `78e6104`（`git ls-tree HEAD` 可验证），已离线取源核对。若要复核，请 checkout 该 submodule。

---

## 0. 结论速览（先看这个）

| 问题 | 结论 | 关键证据 |
|---|---|---|
| 后端能否「稍后再回复」请求？ | **不能。** 上游协议是严格的一问一答，`run()` 同步阻塞读一行、`handleRequest` 同步返回一行 | `python/server.py:98-137`、`python/textService.py:83-141` |
| 按键类 RPC 超时 | **2000 ms** | `PIMETextService/PIMEClient.h:99` |
| 激活类 RPC 超时 | 15000 ms（仅 `onActivate`/启动握手） | `PIMETextService/PIMEClient.h:100`、`PIMEClient.cpp:411` |
| 超时后 C++ 行为 | 关闭管道、标记 Disconnected、清空组合态、自动重连；本次按键 `return false`（不吞键） | `PIMEClient.cpp:454-468`、`PIMEClient.cpp:765-773` |
| 后端「卡死」判定 | Launcher 看门狗：任一挂起请求 ≥ **15 s** → 杀后端进程并重启 | `PIMELauncher/src/protocol.rs:11`、`backend_manager.rs:441-457` |
| 现成后端有没有「延迟回复」用法 | **没有。** 上游唯一的"异步"是**预加载**（cinbase 码表后台线程加载，加载期间直接吃掉按键立即返回），不是延迟回复 | `python/cinbase/__init__.py:3342-3365,3404-3405,436-441` |
| 能否免编译新增一个输入法？ | **能。** 语言配置不是硬编码，`DllRegisterServer` 在 regsvr32 时扫 `backends.json` + 各 `ime.json` 动态生成；新增目录后**重跑 regsvr32（x86 + x64 各一次，管理员）**即可出现在语言列表 | `PIMETextService/DllEntry.cpp:125-193`、`libIME2/ImeModule.cpp:124-155` |
| 后端进程位数 | **32 位**（内置 CPython 3.15.1 win32；PIMELauncher 恒为 x86） | `python/python3/build-details.json`、`_pime/README.md:34`、`CMakeLists.txt:39` |
| 一次能提交多长文本？ | 一次 `setCommitString` 提交**整串**（TSF 单次 `Range::SetText`），不受 IPC 分片限制；受限的是**响应时间**，不是长度 | `PIMEClient.cpp:155-175`、`libIME2/TextService.cpp:260-293` |

---

## 1. 架构与进程模型

```
应用进程 (任意位数)
  └─ PIMETextService.dll (TSF TIP，x86 / x64 / ARM64X 三份)
        │  命名管道  \\.\pipe\<user>\PIME\Launcher      (RpcPipe，同步)
        ▼
  PIMELauncher.exe  (Rust，**恒为 32 位**)
        │  stdin/stdout 行协议
        ▼
  后端进程 (python3\python.exe 32 位 / node.exe / go-backend)
        └─ 每个后端进程内跑 *所有* 该后端的输入法（含我们的）
```

- 客户端是「薄客户端」，业务逻辑全在后端；数据一律 JSON/UTF-8：`_pime/HACKING.txt:3-12`。
- IPC 链路说明（图同上）：`_pime/HACKING.txt:15-29`。
- 管道名：`PIMEClient.cpp:793` `\\.\pipe\<username>\PIME\<baseName>`（调用处 `:709` 用 `Launcher`）；Launcher 侧同名：`PIMELauncher/src/main.rs:323`。
- Launcher 根目录 = `PIMELauncher.exe` 所在目录（即安装目录 `$INSTDIR`，默认 `C:\Program Files (x86)\PIME`）：`main.rs:355-366`、`installer/installer.nsi:59`。
- **只有 32 位构建才编Launcher**：`CMakeLists.txt:39`（`if(CMAKE_SIZEOF_VOID_P EQUAL "4")` 才 `add_subdirectory(PIMELauncher)`）⇒ 后端进程必然是 32 位。这对 `ctypes` 结构体/指针宽度有直接影响（见 §7）。

---

## 2. 第 1 组问题：后端契约

### 2.1 必须实现的类

```python
# _pime/python/textService.py:61-74
class TextService:
    def __init__(self, client):        # l.62  参数是 server.py 的 Client 对象
        self.client = client
        self.isActivated = False
        ...
```

- 由 `serviceManager` 惰性导入并实例化：`importlib.import_module(moduleName)` → `getattr(mod, serviceName)(client)`，见 `python/serviceManager.py:79-92`。
- **唯一必须存在的成员是类本身 + `__init__(self, client)`**；其余钩子全部有默认实现（`textService.py:144-179` 全是 `pass` 或 `return False`），不实现即"不响应"。
- 实用建议：至少实现 `onActivate` / `onKeyDown` / `onDeactivate`，否则输入法装上了也不干活。

### 2.2 请求分发（C++ → Python）

`textService.py:83-141` `handleRequest(msg)`：

| `msg["method"]` | 回调签名 | 说明 |
|---|---|---|
| `init` | — | 由 `server.py:49-57` 拦截，仅创建 service，不转发给 `handleRequest` |
| `filterKeyDown` | `(keyEvent)` → bool | 拦截键（True=吃掉） |
| `onKeyDown` | `(keyEvent)` → bool | 真正处理，返回值决定是否吃掉该键 |
| `filterKeyUp` / `onKeyUp` | `(keyEvent)` → bool | |
| `onPreservedKey` | `(guid)` | guid 已小写（`l.121`） |
| `onCommand` | `(commandId, type)` | |
| `onMenu` | `(id)` | 返回 `(commandId, checked)` 或 dict |
| `onCompartmentChanged` | `(guid)` | |
| `onKeyboardStatusChanged` | `(opened)` | |
| `onCompositionTerminated` | `(forced)` | 默认实现会清空 `commitString`/`compositionString`（`l.175-177`） |
| `onActivate` | `()` | `l.124-127`：置 `isActivated=True`、`keyboardOpen=msg["isKeyboardOpen"]` |
| `onDeactivate` | `()` | `l.128` |

未知方法 → `success=False`（`l.130`，实测确认）。

**真实请求行长这样**（实测抓到的原文，按 `<client_id>|<json>\n` 送入后端 stdin）：

```
aaaaaaaa-1111-2222-3333-444444444444|{"method": "onKeyDown", "seqNum": 6, "charCode": 101, "keyCode": 65, "repeatCount": 1, "scanCode": 30, "isExtended": false, "keyStates": [0, 0, ...共 256 字节...]}
```

- `client_id` = ime.json 的 profile guid（`PIMEClient.cpp:58,709`、`protocol.rs:19-35`）。
- 按键字段由 `addKeyEventToRpcRequest` 打包（`PIMEClient.cpp:78-90`）；`KeyEvent` 的解析在 `textService.py:36-58`（`isKeyDown/isChar/isPrintableChar/isSymbols` 可直接用）。
- `onActivate` 的请求里额外带 `isKeyboardOpen`（`textService.py:124-127`）。

### 2.3 回复结构（Python → C++）

`textService.py:135-141` 固定组装：

```json
{"commitString": "...", "compositionString": "...", "candidateList": [...], "return": true, "success": true, "seqNum": 3}
```

- `currentReply` 是**每请求清空**的临时字典（`l.135-136`），所以**一次按键只能回一次**，没有跨请求累积的可能。
- `success` 为 false 时 C++ 直接丢弃整个回复且不 updateStatus（`PIMEClient.cpp:92-101`），**失败回复没有任何副作用通道**。
- `seqNum` 由 C++ 递增下发（`PIMEClient.cpp:749`），C++ 会校验回复里的 `seqNum` 是否与请求一致，不一致即视为失败（`PIMEClient.cpp:759-762`）。

### 2.4 `currentReply` 字段 → C++ 消费者 → TSF 落地

消费总入口：`PIMEClient::handleRpcResponse` → `updateStatus`，执行顺序固定为：
`setSelKeys → showMessage/hideMessage → customizeUI → (有 session 时) candidateList → commitString → composition → 语言栏按钮 → 保留键 → keyboardStatus`（`PIMEClient.cpp:316-348`）。

| Python 写入 API | reply 键（注意不等名） | C++ 消费点 | 最终 TSF 行为 |
|---|---|---|---|
| `setSelKeys(keys)` | `setSelKeys` | `updateSelectionKeys` `PIMEClient.cpp:124-131` | 候选选择键 |
| `showMessage(msg, duration=3)` | `showMessage:{message,duration}` | `updateMessageWindow` `:135-144` | libIME 提示窗（duration 单位**秒**）；未组合时会先 `startComposition` |
| `hideMessage()` | `hideMessage:true` | `:149-151` | 关提示窗 |
| `customizeUI(**kw)` | `customizeUI:{}`（candFontName/candFontSize/candPerRow/candUseCursor） | `updateUI` `:326-329`、`updateUI` `:104` | 候选窗外观 |
| `setCandidateList(list)` | `candidateList` | `updateCandidateList` `:350-...` | 直接替换 `candidates_`；`showCandidates=true` 且未组合时先 `startComposition` |
| `setCandidateCursor(i)` | `candidateCursor` | 同上 | 当前高亮项 |
| `setShowCandidates(bool)` | `showCandidates` | 同上 | 显示/隐藏候选窗 |
| `setCommitString(s)` | `commitString` | `updateCommitString` `:155-175` | **一次性提交整串**（见 2.5） |
| `setCompositionString(s)` | `compositionString` | `updateComposition` `:177-232` | 组合区文本；空串且未显示候选 → 结束组合 |
| `setCompositionCursor(i)` | `compositionCursor` | 同上（`compositionCursorToUtf16Position` 处理代理对，`:217-226`） | 光标位置 |
| `addButton(id, **kw)` | `addButton:[{...,"id"}]` | `updateLanguageButtons` `:234-...` | 语言栏按钮（`icon/commandId/text/tooltip/type/enable/toggled`，`textService.py:196-200`） |
| `removeButton(id)` / `changeButton(id, **kw)` | `removeButton` / `changeButton` | 同上 | |
| `addPreservedKey(keyCode, modifiers, guid)` | `addPreservedKey:[{keyCode,modifiers,guid}]` | `updatePreservedKeys` `:279-...` | 全局热键（guid 小写，`textService.py:216-221`） |
| `removePreservedKey(guid)` | `removePreservedKey:[guid]` | 同上 | |
| `setKeyboardOpen(b)` | **`openKeyboard`**（名字不一致！） | `updateKeyboardStatus` `:310-312` | 软键盘开关 |

### 2.5 `setCommitString` 落到 TSF 的确切路径

1. `PIMEClient::updateCommitString`（`PIMEClient.cpp:155-175`）：`commitString` 非空 → 若当前未组合则先 `startComposition()` → `textService_->setCompositionString(session, str, len)` → `endComposition(session->context())`。
2. `libIME2@78e6104 src/TextService.cpp:260-293` `setCompositionString`：`context->GetSelection` → `composition_->GetRange(&compositionRange)` → **`compositionRange->SetText(editCookie, TF_ST_CORRECTION, str, len)`**（`l.274`）→ 光标移到组合末尾（`l.277-278`）→ 设 `GUID_PROP_ATTRIBUTE` 显示属性（`l.282-288`）。
3. `src/TextService.cpp:202-236` `endComposition`：清显示属性 → `ShiftEndToRange(.., TF_ANCHOR_END)` + `Collapse` + `SetSelection`（`l.221-223`）→ **`composition_->EndComposition(cookie)`**（`l.228`）→ `onCompositionTerminated(false)`（`l.230`）。
4. 所有编辑都在 `context->RequestEditSession(clientId_, editSession, TF_ES_SYNC|TF_ES_READWRITE, &result)` 里同步执行（`src/TextService.cpp:199`）。

**含义**：提交 = 「建组合 → 一次 SetText 写整串 → 结束组合」。**长文本一次提交不会因为长度失败**，也不会被拆成多次 IPC；被 IPC 时间限制的是「算多久」，不是「写多长」。

### 2.6 生命周期

- 应用激活输入法：Launcher 收客户端 `init`（`{"method":"init","id":<profile guid>}`，`protocol.rs:19-35`）→ 转发给后端 → 后端 `server.py:49` 建 `Client` → `serviceManager.load` 查 guid → 实例化 TextService → 回 `{"seqNum":1,"success":true}`。
- 随后 C++ 在 `startAsyncConnect` 的回调里再同步发一次 `onActivate`（`PIMEClient.cpp:719-724`，超时 2000 ms）。
- 应用退出/切走：`onDeactivate`（`PIMEClient.cpp:420-436`）。
- 客户端断开：Launcher 发 `{"method":"close","seqNum":last+1}` 给后端（`backend_manager.rs:93-111`）；上游 `server.py:125-131` 对 `close` 只回 `{"seqNum":n,"success":true}`，**不会调用任何 Python 钩子**（不会触发 `onDeactivate`）。
- 每个客户端（每个应用窗口）= 一个独立的 TextService 实例；但**所有实例共享同一个后端进程和同一个单线程 stdin 循环**（`python/server.py:98-137`）。

### 2.7 最小可用后端骨架

```python
# 文件：<INSTDIR>\python\input_methods\cips\cips_ime.py
from textService import TextService

class CipsTextService(TextService):
    def onActivate(self):
        # 只做轻量初始化；重活留到后台线程（见 §3.6）
        pass

    def onKeyDown(self, keyEvent):
        if keyEvent.charCode == 0x1b:          # ESC
            return False
        self.setCommitString("示例")            # 一次性提交
        return True                             # True = 吃掉该键
```

```json
// 文件：<INSTDIR>\python\input_methods\cips\ime.json
{
  "name": "cips 输入法",
  "version": "0.1",
  "guid": "{xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx}",
  "locale": "zh-Hans-CN",
  "fallbackLocale": "zh-CN",
  "icon": "icon.ico",
  "win8_icon": "",
  "moduleName": "cips_ime",
  "serviceName": "CipsTextService"
}
```

---

## 3. 第 2 组问题：异步 / 延迟回复（全局最关键）

### 3.1 结论（先给答案）

**PIME 2.0.0-beta4 没有「延迟回复」机制，也没有任何异步回复 API。**

- 后端主循环是**同步阻塞**的：`python/server.py:100-137`，`while True: line = input()` → `client.handleRequest(msg)` → 立刻 `print(reply_line)`。
- 回复内容完全由 `handleRequest` 的**返回值**决定（`textService.py:135-141`），函数返回即发包，没有回调、没有 queue、没有 seqNum 延后匹配。
- Launcher 只按 `client_id` 路由，**不按 seqNum 配对**：`PIMELauncher/src/backend_manager.rs:335-355`、`client_session.rs:49`。所以「迟到的回复」不会被丢弃，而会被当作**当前正在等回复的那个请求**的应答 → C++ 校验 seqNum 不符 → 判定失败（`PIMEClient.cpp:759-762`）。

实测（本机，见 §3.5）：请求 → 回复的对应关系是 1:1 且同步的，一次 `time.sleep(3.0)` 就让该请求的回复晚了 3000 ms。

### 3.2 所有相关超时（毫秒级，逐个给证据）

| 常量/位置 | 值 | 作用 | 证据 |
|---|---|---|---|
| `kDefaultRpcTimeoutMs` | **2000 ms** | 所有按键/菜单/状态类 RPC 的等待上限 | `PIMETextService/PIMEClient.h:99`；`callRpcMethod(..., timeoutMs = kDefaultRpcTimeoutMs)` 默认参数 `PIMEClient.h:103` |
| `kServerStartupTimeoutMs` | 15000 ms | 仅 `onActivate` | `PIMEClient.h:100`、`PIMEClient.cpp:411` |
| 管道连接超时 | 3000 ms | `RpcPipe::connect` 默认 | `PIMETextService/RpcPipe.h:48` |
| 异步连接握手超时 | 15000 ms | AsyncConnector 的 init RPC | `PIMETextService/AsyncConnector.cpp:34`、`:263` |
| `HANG_TIMEOUT` | 15 s | Launcher 看门狗：挂起请求超时判定后端 hang | `PIMELauncher/src/protocol.rs:11`、`backend_manager.rs:441-457` |
| `BACKEND_WRITE_TIMEOUT` | 5 s | Launcher 往后端 stdin 写的超时，超时即杀后端重启 | `protocol.rs:14`、`backend_manager.rs:427-432` |
| 客户端管道写超时 | 500 ms | Launcher 往 TSF 客户端写回复 | `backend_manager.rs:355-362` |
| `MAX_MESSAGE_LINE_LENGTH` | 1 MB | 单行长度上限 | `protocol.rs:5` |
| `kMaxIpcMessageLength` | 4 MB | C++ 侧单条消息上限 | `RpcPipe.h:31`、`AsyncConnector.cpp:35` |

**安全线建议**：单次 handler 内部耗时 ≤ 500 ms。理由：2000 ms 是硬上限（且是"从请求写出到收到整行"的全链路，不含调度抖动、杀毒软件扫描、JSON 序列化），UI 线程在这段时间是**阻塞**的（`TF_ES_SYNC`），超过 100 ms 用户就开始感知卡顿。

### 3.3 各方法对应的超时（C++ 调用点，全部默认 2000 ms，除注明外）

`PIMEClient.cpp`：`onDeactivate:431`、`filterKeyDown:447`、`onKeyDown:463`、`filterKeyUp:479`、`onKeyUp:495`、`onPreservedKey:513`、`onCommand:531`、`sendOnMenu:546`、`onCompartmentChanged:657`、`onKeyboardStatusChanged:673`、`onCompositionTerminated:688`。
例外：`onActivate` 走 15000 ms（`:411`）。

### 3.4 如果 `onKeyDown` 里 `sleep(3000)` 再返回，会发生什么

按时间顺序（全部有源码依据）：

1. **0 ms**：用户按键 → TSF `OnKeyDown` → `PIMEClient::onKeyDown` → `callRpcMethod` 写一行到管道（`PIMEClient.cpp:459-463`）。
2. **0→3000 ms**：后端 sleep。**应用的 UI 线程被这个同步 RPC 阻塞**（TSF 里 `RequestEditSession` 是 `TF_ES_SYNC`，`libIME2 src/TextService.cpp:199`）⇒ 该应用卡死 3 秒。
3. **2000 ms**：`RpcPipe::callPipeIO` 的 `WaitForSingleObject(ioEvent_, 2000)` 超时 → `CancelIoEx` + `GetOverlappedResult(...,TRUE)` → `ok=false`（`RpcPipe.cpp:88-121`），`callRpc` 返回 false（`RpcPipe.cpp:124-154`）。
4. **2000 ms**：`callRpcMethod` 判失败 → `response=null`、`pipe_.close()`、`state_=Disconnected`、`resetTextServiceState()`（清组合/候选/消息窗）、`startAsyncConnect()` 重连（`PIMEClient.cpp:765-773`）。
5. **2000 ms**：`handleRpcResponse` 拿到 null → 返回 false → `onKeyDown` `return false`（`PIMEClient.cpp:464-467`）⇒ **该按键未被吃掉，字符原样输入到应用**（比如按 `s` 会直接上屏 `s`）。
6. **2000 ms 之后**：C++ 侧 `nextSeqNum_` 与新连接重新从 1/2 开始；组合态、候选、语言栏状态全部丢失（`PIMEClient.cpp:713`、`resetTextServiceState`）。
7. **3000 ms**：后端终于 `print` 那行回复 → Launcher 按 `client_id` 找到"新连接"（旧管道已关闭，client 可能已 unregister）→ 要么 `warn` 丢弃（`backend_manager.rs:363-367` 未知 client），要么**被当作下一个请求的回复**从而 seqNum 不符，触发又一轮失败/重连（`PIMEClient.cpp:759-762`）。
8. **15 s**（若后端持续不回复）：Launcher 看门狗判定 hang → **杀进程并重启后端**（`backend_manager.rs:441-461`）⇒ 所有应用的所有输入法状态归零。

**结论**：`sleep(3000)` 不是"慢一点"，而是**功能错误**：按键漏上屏、输入法状态丢失、连带重连。

### 3.5 实测数据（本机真实运行，非推断）

环境：`_pime/python/python3/python.exe`（3.15.1 32-bit）跑 `server.py` 副本（`%TEMP%\pime_ipc_test\`），驱动脚本按 `<client_id>|<json>\n` 写入 stdin，读 `PIME_MSG|...` 行并打时间戳。假 IME：`s` 键 `time.sleep(3.0)`；`e` 键抛 `RuntimeError`；其余立即 `setCommitString("fast")`。

| 相对启动 | 事件 | 回复 |
|---|---|---|
| 138 ms | `init` (CID1) | `{"seqNum":1,"success":true}` |
| 139 ms | `onActivate` | `{"success":true,"seqNum":2}` |
| 139 ms | `onKeyDown` 'a' | `{"commitString":"fast","return":true,"success":true,"seqNum":3}`（**同一毫秒**） |
| 139 ms | `onKeyDown` 's'（sleep 3 s） | —（挂住） |
| 150 ms | `init`（**另一个应用 CID2**） | —（挂住） |
| 3139 ms | 上条 's' 的回复 | `{"commitString":"slow",...,"seqNum":4}`（**精确 +3000 ms**） |
| 3140 ms | CID2 的 `init` 回复 | `{"seqNum":1,"success":true}` ⇒ **被别人的慢请求阻塞了 ~3 秒** |
| 3140 ms | `onKeyDown` 'a' | 正常 `{"commitString":"fast",...}` ⇒ 慢请求不杀后端 |
| 3180 ms | `onKeyDown` 'e'（抛异常） | `{"success":false,"seqNum":6}`，stderr 打印 `ERROR: boom <原始行>` + Traceback，**进程存活** |
| 3180 ms | 未知方法 | `{"success":false,"seqNum":7}` |

三条硬结论：
1. **一问一答，绝无延迟回复**：每个请求恰好一行回复，回复时机 = handler 返回时机。
2. **单线程串行**：一个应用的慢请求会阻塞**其它所有应用**的请求（上表 3140 ms 那行）。这是"全局"影响，不只是自己卡。
3. **异常安全**：handler 抛异常 → 该请求回 `success:false` + 写 `python_backend.log`，后端不退出（`server.py:141-158`）。但**不能拿异常当"延迟"手段**，因为客户端看到的是失败。

### 3.6 上游唯一存在的"异步"范式：预加载 + 立刻返回（不是延迟回复）

cinbase 系列（行列输入法）要加载几十 MB 码表，它们的做法值得直接抄：

- 码表加载放到线程池：`cinbase/__init__.py:3368` `ThreadPoolExecutor(thread_name_prefix="CinTableLoader")`；`LoadCinTable.start()` 提交任务并挂 `add_done_callback`（`:3404-3405`），完成回调里把结果塞给 service：`self.cbTS.cin = res`（`:3394-3402`）。
- 短语库用 `threading.Thread`：`class LoadPhraseData(threading.Thread)`（`:3342-3365`），`onActivate`/init 时 `start()`（`:3119-3121`），不等结果。
- 状态通过 `future.done()` 暴露：`cintablebase.py:25-61` `loading` / `cin` / `wait(timeout)`。
- **关键**：`onKeyDown` 里发现还没加载完，就**立刻吃掉按键并返回**，绝不等待：
  ```python
  # cinbase/__init__.py:436-441
  if CinTable.loading or not getattr(cbTS, 'cin', None):
      ...
      cbTS.showMessage('正在載入輸入法碼表，請稍候...')
      return True
  ```
- 各 IME 只在非 loading 时才会切表：如 `chepinyin_ime.py:61` `if not CinTable.curCinType == self.cfg.selCinType and not CinTable.loading:`

⇒ 这就是"**用下一次按键来取上一次异步结果**"的模式：**不是回复延迟，而是把等待摊到多次按键上**，并且明确告诉用户"正在加载"。

### 3.7 cips 可用的替代方案（按推荐度排序）

| 方案 | 做法 | 优点 | 风险/限制 |
|---|---|---|---|
| **A. onActivate 预计算（首选）** | 激活时把模型/词库/索引一次性加载到内存（后台线程 + `loading` 标志），按键路径只做查表 | 与上游 cinbase 同构，最稳；按键耗时 <10 ms | 首次激活有冷启动；加载期间必须照抄 §3.6 的"吃键 + showMessage" |
| **B. 每键小步（增量）** | 把长任务切成每次按键只推进一小步（状态存实例变量） | 天然满足 2000 ms | 状态机复杂度上升；用户"一按到底"时体验受限 |
| **C. 后台线程 + 立即返回，结果由后续请求带出** | handler 里 `threading.Thread(...).start()` 后立刻 `return`；线程算完后写入 `self._pending`，下一次 `onKeyDown` 或 `onCompositionTerminated` 时把结果 `setCommitString` 出去 | 完全在协议内；不阻塞任何应用 | **不能主动推送**（见 3.1 的 client_id 路由说明）；必须自己保证线程不碰 `currentReply`（跨请求写会被清空） |
| **D. 自写 server.py 实现"延迟回复"** | 不用上游 `server.py`，自己实现行协议：收到请求先不打印，后台算完再打印同一 `seqNum` | 技术上可行（Launcher 只认 `PIME_MSG\|cid\|json` 行） | **收益为零**：C++ 仍阻塞在 `callRpc` 上等这一行，UI 线程照样冻结；且超过 2000 ms 即失败。只适合把"计算"和"其它客户端请求"解耦（自建多线程 server），不解决长耗时 |
| **E. SendInput 通道（cips 原设计）** | handler 里只做"投递任务"后立即返回；真正的逐字 `SendInput` 在**后台线程**里做，配合 `sleep(几十 ms)` | 能绕开 2000 ms（因为回复早已发出） | ① 后台线程的 `SendInput` 会打进**当前前台窗口**，用户切窗口即错投；② 与 TSF 自身提交的字符可能重复/乱序（`updateCommitString` 已由 TSF 写一次）；③ 目标窗口若提权（UIPI）则 `SendInput` 无效；④ 后台线程崩溃会连累整个后端（可用 `try/except` 包住）；⑤ 必须保证答案在 reply 里给 TSF 的 `commitString` 与 `SendInput` 内容不重复 |
| **F. 改 C++ 客户端超时（如 2000→10000）** | 需要重编 `PIMETextService.dll` | 根治 | 本机无 VS2026/Rust（见 §5.2），**当前不可行**；且仍然会冻结 UI 线程 10 s，体验更差 |

**给 cips 的建议**：走 A + C 组合。**永远不要**在 `onKeyDown` 里做网络/模型推理/文件 IO；`setCommitString` 一次性提交整串（§2.5 证明长度无限制）。如果确实需要"逐字上屏"的观感，用**多次按键驱动多次提交**（每次按键提交一个字），而不是一次按键里 sleep 循环。

> **0.2 / 0.3 实践修正**：A + C 组合在"热键触发"场景里不成立——热键经
> `filterKeyDown` / `onPreservedKey` 到达，两条路径都**没有编辑会话**，
> `setCommitString` 会被静默丢弃（§2.5 的 `if (session != nullptr)`），而
> `onKeyDown` 只有在 `filterKeyDown` 返回 false 时才会被调用。所以最终实现
> 只能走方案 E（后台线程 + `SendInput` 逐字注入），并针对 E 的风险做了三项
> 加固：注入前后比对目标 `hwnd`、`Esc` 取消、后台线程全程 `try/except`。
> 当前实现见 `src/input_methods/cips/cips_ime.py`，本文件其余部分仍是 v0.1
> 阶段的调研快照。

---

## 4. 第 3 组问题：ime.json 字段与 TSF 注册

### 4.1 ime.json 字段（谁读、是否必需）

| 字段 | 必需 | 读取方 | 语义 |
|---|---|---|---|
| `name` | **必需**（为空则跳过） | `DllEntry.cpp:94-98`（空则 `return nullopt` ⇒ 该输入法不注册） | 语言列表里显示的名字 |
| `guid` | **必需** | `DllEntry.cpp:95,100-103`（`CLSIDFromString` 校验） | **输入法 profile GUID**，必须全局唯一；客户端 `init` 用它找后端（`protocol.rs:19-35`） |
| `locale` | 建议必需 | `DllEntry.cpp:106`；`libIME2/ImeModule.cpp:131` | 语言区域名（如 `zh-Hans-CN`），`LocaleNameToLCID` 失败则用 `fallbackLocale`（`:132-136`） |
| `fallbackLocale` | 可选 | 同上 | locale 兜底 |
| `icon` | 可选 | `DllEntry.cpp:108-114` | 图标文件名，**相对 ime.json 目录**会被拼成绝对路径 |
| `iconIndex` | 可选（非 ime.json 字段，由系统决定） | `DllEntry.cpp:126-129` | Win8+ 用 1，否则 0 |
| `win8_icon` | **无人读取** | —（`DllEntry.cpp` 只取 `icon`） | 示例里存在但当前代码未用（`rime/ime.json`）；写不写无所谓 |
| `version` | 可选 | 仅 Python 侧元数据（`serviceManager.py:56-77` 不用它） | 无功能作用 |
| `moduleName` | **必需** | `serviceManager.py:72` | 模块名；实际 import 路径 = `input_methods.<目录名>.<moduleName>`（`serviceManager.py:27-41,65-73`） |
| `serviceName` | **必需** | `serviceManager.py:75` | 模块里的类名 |
| `configTool` | 可选 | `PIMEImeModule.cpp:159`（还有 `configToolParams:160`、`configToolDir:161`） | 右键"设置"时 `ShellExecuteW` 启动的命令（`:150-189`）；相对路径会 `PathCanonicalize` 成绝对（`:163-174`） |

C++ 侧只从每个 `ime.json` 里读 5 个键：`name`/`guid`/`locale`/`fallbackLocale`/`icon`（`DllEntry.cpp:94-114`）；`configTool*` 由 `ImeModule::loadImeInfo` 在运行时另读（`PIMEImeModule.cpp:97-126,156-161`）。

### 4.2 注册链路（安装时到底发生了什么）

1. `installer.nsi:458-476`：把 DLL 装到 `$INSTDIR\x64`、`$INSTDIR\x86`（ARM64 走 `PIMETextService_arm64.dll` / ARM64X），并对每个架构 `!insertmacro RegisterTextService <arch> <dll>`。
2. `installer/macros.nsh:155-163`：`RegisterTextService` = 关掉 x64 文件系统重定向后用对应架构的 `regsvr32.exe /s <dll>`。（`UnregisterTextService` 加 `/u`，`:179-187`。）
3. `DllEntry.cpp:125-193` `DllRegisterServer`：读完 `backends.json`（`PIMEImeModule.cpp:69-84` 只取每项 `name`）→ 对**每个后端目录**扫 `<programDir>\<backend>\input_methods\*\ime.json`（`DllEntry.cpp:136-166`）→ 收集 `LangProfileInfo{name, clsidGuid, locale, fallbackLocale, iconFile, iconIndex}`（`:82-123`）→ `module->registerServer(L"PIMETextService", profiles, count, modulePath)`（`:193`）。
4. `programDir` = `SHGetFolderPathW(CSIDL_PROGRAM_FILESX86)`（失败退回 `PROGRAM_FILES`）+ `\PIME`（`PIMEImeModule.cpp:57-66`）⇒ **必须是 `C:\Program Files (x86)\PIME`（或 Program Files\PIME）**，装到别处不会被扫到。
5. `libIME2@78e6104 src/ImeModule.cpp:124-155` `registerLangProfiles`：`ITfInputProcessorProfiles::Register(textServiceClsid_)`（`:130`）→ 每个 profile `AddLanguageProfile(clsid, langId, profileGuid, name, ..., iconFile, ..., iconIndex)`（`:139-141`）→ `EnableLanguageProfileByDefault(TRUE)`（`:147`）、`EnableLanguageProfile(TRUE)`（`:148`）。
6. `:157-232`：**额外手写未公开注册表**，把输入法挂进 Win10/11 的语言列表：
   `HKEY_USERS\<SID>\Control Panel\International\User Profile\<locale>`，值名 `<lcid 4位小写 hex>:<TSDLL的CLSID><profile GUID>` = DWORD（`:197-221`）；仅 32 位 DLL 且 Win8+ 执行（`:172,176`）。
7. `:247-281` `registerServer`：写 `HKCR\CLSID\{35F67E9D-A54D-4177-9697-8B0AB71A9E04}`（默认值 = `"PIMETextService"`）+ `InprocServer32`（默认值 = DLL 全路径，`ThreadingModel = Apartment`）。
8. `:299-330`：注册 TSF 类别 `GUID_TFCAT_TIP_KEYBOARD`、`DISPLAYATTRIBUTEPROVIDER`、`TIPCAP_INPUTMODECOMPARTMENT`、`TIPCAP_UIELEMENTENABLED`、Win8+ `TIPCAP_IMMERSIVESUPPORT`、`TIPCAP_SYSTRAYSUPPORT`。

**关键点：所有后端/输入法共用同一个 TSF CLSID `{35F67E9D-A54D-4177-9697-8B0AB71A9E04}`**（`PIMEImeModule.cpp:40-41`），区分靠 `AddLanguageProfile` 里的 profile GUID（即 ime.json 的 `guid`）。这也是为什么 `PIMEClient.cpp:58` 用 profile guid 作为 `client_id` 发给后端。

### 4.3 能否手动放一个新后端目录就生效？（核心问题）

**能，且不需要重新打包安装程序。** 因为注册扫描发生在 `regsvr32` 那一刻，由 `DllRegisterServer` 现场读目录生成。

最简步骤（管理员 PowerShell / CMD）：

```powershell
$inst = "C:\Program Files (x86)\PIME"          # 必须与 programDir 一致
# 1) 放文件：<inst>\python\input_methods\cips\{ime.json, cips_ime.py, ...}
#    （Python 后端 = backends.json 里的 "python"，所以放 python\input_methods\ 下）
# 2) 重新注册（两个架构都要，缺一会导致 64 位应用里看不到）
C:\Windows\System32\regsvr32.exe /s "$inst\x64\PIMETextService.dll"
C:\Windows\SysWOW64\regsvr32.exe /s "$inst\x86\PIMETextService.dll"
# 3) 重启语言栏宿主，让 TSF 重新加载 profile
& "$inst\PIMELauncher.exe" /quit
Start-Process "$inst\PIMELauncher.exe"
```

- 停 Launcher：`PIMELauncher.exe /quit`（`HACKING.txt:33-52`），或 `macros.nsh:215-221` 里的 `/quit` + `taskkill /F /IM PIMELauncher.exe /T`。
- 是否需要 `InstallLayoutOrTip`？**不需要**——PIME 自己走 `ITfInputProcessorProfiles` + 手写 `User Profile` 注册表（`libIME2 ImeModule.cpp:124-232`），installer 里也没有任何 `InstallLayoutOrTip` 调用。
- 若只是**改已有输入法**（比如换后端实现、改代码），连 regsvr32 都不用，只要重启 Launcher；但**新增/删除 ime.json（= 新增 profile）必须重跑 regsvr32**。
- 若 `name` 为空或 `guid` 非法，该 ime.json 会被**静默跳过**（`DllEntry.cpp:94-103`），语言列表里就看不到——这是最常见的"我放了目录却没出现"。
- 目录必须在 `python\input_methods\` 下（不是 `input_methods\`），因为扫描根是 `<programDir>\<backend name>\input_methods\*`（`DllEntry.cpp:136-166`），`backend name` 来自 `backends.json`（`_pime/backends.json` 里是 `python`/`node`/`go-backend`）。

副作用与坑：
- 重跑 regsvr32 会重建 `HKCR\CLSID\{...}` 与类别注册；卸载时 `unregisterServer` 会把 `User Profile` 下属于该 CLSID 的值全删（`ImeModule.cpp:385-437`，`:419` 按 CLSID 字符串匹配）⇒ **会连带删掉其它 PIME 输入法的语言配置**，重装后需要重新在语言列表里启用。
- 装到非 `Program Files (x86)\PIME` 会扫不到（`PIMEImeModule.cpp:61-66`）。
- 写安装目录需要管理员；**运行时数据（模型/缓存/日志）必须写到 `%LOCALAPPDATA%`**，否则在 Program Files 下无写权限。

---

## 5. 第 4 组问题：安装与运行方式

### 5.1 官方 Release（实测 GitHub API，走代理）

| 版本 | 类型 | 资源 |
|---|---|---|
| v2.0.0-beta4（2026-10-23） | prerelease | `PIME-2.0.0-beta4-setup.exe` |
| v2.0.0-beta3 / beta2 / beta | prerelease | 各自 `PIME-<ver>-setup.exe` |
| v1.3.0-stable（2023-01-20） | stable | `PIME-1.3.0-stable-setup.exe` |

下载地址形式：`https://github.com/EasyIME/PIME/releases/download/v2.0.0-beta4/PIME-2.0.0-beta4-setup.exe`。
安装包特性：`installer.nsi:59` 默认 `$PROGRAMFILES32\PIME`；`:62` 要求管理员；`:200` 最低 Win10；结束时会以当前用户身份启动 `PIMELauncher.exe`（`:498`）。

### 5.2 上游编译要求 vs 本机（"完全跳过编译"是否成立）

上游要求（`README.md:19-24`、`build.bat:1`）：
- CMake ≥ 4.2（`Visual Studio 18 2026` 生成器从 4.2 才有）
- **Visual Studio 2026** + "Desktop development with C++"
- **Rust stable + `i686-pc-windows-msvc`**（Launcher 是 32 位；`README.md:34-36`）
- Node.js + npm（仅用于打包内置 JS 输入法）
- NSIS 3.x（仅打包安装程序）

本机实测：`cmake 4.4.4` ✓；**`vswhere -all -prerelease` 返回 `[]`（未装任何 Visual Studio）**；**`rustc` 不存在（未装 Rust）**；git 2.56.0 ✓。

⇒ **"自己编译整套"当前不可行**（要装 VS2026 + Rust + 配置 i686 target，且 `build.bat` 还要 npm 构建 4 个 JS 输入法）。
⇒ **"完全跳过编译"成立**：用官方 `PIME-2.0.0-beta4-setup.exe` 装好，然后按 §4.3 放目录 + regsvr32。这条路不碰任何 C++/Rust 代码。

### 5.3 内置 Python 与依赖

- `_pime/python/python3/build-details.json`：`platform = "win32"`、`language.version = "3.15"`（3.15.1 final）、`cache_tag = cpython-315`、`extension_suffix = .cp315-win32.pyd`、`libpython.dynamic = python315.dll` ⇒ **嵌入式 CPython 3.15.1，32 位**。
- 实机 `python.exe -V`：`Python 3.15.1 ... [MSC v.1951 32 bit (Intel)]`。
- `sys.path` = `python315.zip` / `python3` / `python3\Lib\site-packages` / `$INSTDIR\python`（最后一项来自 `site-packages\PIME.pth`，内容为 `../../..`，注释 `# PIME: add parent python dir`）⇒ 所以 `import textService` 可用。
- `python315._pth` 内容含 `import site` ⇒ **site 已启用，`-S` 也无法阻止 `.pth` 生效**（实测：`-S` 仍能看到被 `_pth` 注入的 `$INSTDIR\python`）。
- 第三方包：`requirements.txt` 只有 `tornado>=6.0`；`Lib\site-packages` 下只有 `tornado/` 与 `PIME.pth` ⇒ **除 tornado 外只有标准库**。实测下列标准库均可用：`json/ctypes/sqlite3/ssl/concurrent.futures/threading/queue/zlib`。
- `Lib\` 下没有完整标准库目录树（只有 `site-packages`），标准库来自 `python315.zip` ⇒ 纯 .py 模块（如 `unittest`）可 import，但**不要指望 `pip`/`ensurepip`/编译扩展**。`ctypes` 有（`_ctypes.cp315-win32.pyd` 存在）。
- 后端启动环境：Launcher 注入 `PYTHONIOENCODING=utf-8:ignore`、`PYTHONUNBUFFERED=1`（`backend_manager.rs:305-306`），`CREATE_NO_WINDOW`，cwd = `$INSTDIR\python`（`backend_manager.rs:278-310`）。
- **脚本所在目录不会自动进 `sys.path`**（实测：`import sib` 失败，`PYTHONPATH` 为空）⇒ 后端若要 import 自己目录下的兄弟模块，需自行 `sys.path.insert(0, os.path.dirname(__file__))`。

### 5.4 后端进程位数（影响 ctypes）

- PIMELauncher 恒为 **x86**（`CMakeLists.txt:39` 只在 32 位构建里编 launcher；`README.md:34`）。
- 后端是 Launcher 的子进程（`backend_manager.rs:278-310`），因此 **后端 = 32 位进程**，`python.exe` 也是 win32 构建（`build-details.json`）。
- 结论：**ctypes 里所有 `POINTER`/句柄/`size_t` 都是 4 字节**；`c_void_p` 传整数句柄没问题，但含指针的结构体（如 `BITMAPINFOHEADER`、自定义 `STRUCT` 里嵌 `LPWSTR`）必须按 32 位布局定义；**不能加载 64 位 DLL**（也无法 `ctypes.CDLL` 64 位库）。
- 注意：TSF DLL 本身在**应用进程**里，x86/x64/ARM64 三份（`installer.nsi:458-476`、`PIMETextService/CMakeLists.txt:34-46`），这与后端位数互不影响。

---

## 6. 第 5 组问题：运行时与调试

### 6.1 日志都写在哪

| 内容 | 位置 | 证据 |
|---|---|---|
| Python 未捕获异常 / 请求处理失败 | `%LOCALAPPDATA%\PIME\Logs\python_backend.log`（追加，utf-8） | `python/server.py:32-37`；调用点 `:141-158` |
| Launcher 自身日志（含后端 stdout/stderr 转发） | 默认 **完全抑制**；`PIMELauncher.exe /console` 打到控制台；`/log-file <path>` 写文件 | `HACKING.txt:33-52`；`PIMELauncher/src/main.rs:117-146,188-219` |
| 后端 stderr 每一行 | 以 `[<backend> stderr]` 形式记入 Launcher 日志（`error!` 级别，DEBUG 构建可见） | `backend_manager.rs:263-275,216-219` |
| 后端 stdout 原始行 | 仅 `debug!("Backend Output Raw: ...")`，非 `PIME_MSG|` 前缀的行被丢弃 | `backend_manager.rs:334-337` |

**开发时看 `print` 与 Python traceback 的推荐姿势**：

```powershell
# 1) 先停掉后台实例
& "C:\Program Files (x86)\PIME\PIMELauncher.exe" /quit
# 2) 前台控制台模式启动（日志级别升到 DEBUG，能看到后端 stdout/stderr 转发）
& "C:\Program Files (x86)\PIME\PIMELauncher.exe" /console
```

- 有 `/console` 或 `/log-file` 时日志级别为 DEBUG，否则 WARN（`main.rs:188-219`）。
- 后端 **`print()` 是安全的**：非 `PIME_MSG|` 前缀的行被 Launcher 丢弃（`backend_manager.rs:335-337`），不会污染协议（上游 `rime_ime.py:138,141` 就在裸 print）。但它只在 `/console` 的 DEBUG 日志里可见，**不要用 stdout 传数据**。
- **`stderr` 更安全**：被独立线程逐行排空（`backend_manager.rs:263-275`），不会塞满管道导致后端卡死；因此 `traceback.print_exc()` / `logging.StreamHandler(sys.stderr)` 都能用。
- **`stdin` 绝对不能用**：那是协议通道（`server.py:106` `input()`），自己读会抢走请求。
- 请求级异常会同时写 `python_backend.log` 并回 `{"success":false,"seqNum":n}`（实测确认），是排查"按键没反应"的第一现场。

### 6.2 用户如何切到我们的输入法

1. 装好后，语言列表里会出现 ime.json 的 `name`（因为 `AddLanguageProfile` + `User Profile` 注册表，§4.2）。
2. 在「设置 → 时间和语言 → 语言和区域」里给对应语言**添加键盘**（若未自动出现）；`EnableLanguageProfileByDefault(TRUE)` 通常让它自动启用（`libIME2 ImeModule.cpp:147`）。
3. 切换：`Win + Space` 轮换输入法，或点任务栏语言栏（PIME 自带语言栏按钮，见 `PIMETextService/PIMELangBarButton`）。
4. 首次使用某输入法时 Launcher 才会 spawn 对应后端进程（惰性，`backend_manager.rs:134` `get_backend_input` 惰性 spawn）。

---

## 7. 对 cips 后端的硬约束

> 以下每条都来自上面已取证的源码/实测，属于"违反就坏"的级别。

**协议与并发**
1. **一问一答，禁止延迟回复**。任何请求必须在 **≤2000 ms** 内返回（安全线 500 ms），否则按键漏上屏 + 状态重置 + 重连（`PIMEClient.h:99`、`PIMEClient.cpp:765-773`）。
2. **单进程、单线程串行处理所有应用**。一个 handler 卡住会冻结**所有**应用的输入（实测 §3.5）。**禁止**在任何 handler 里做：网络请求、模型推理、磁盘全量扫描、`sleep`、等待锁。
3. **禁止自己读写 `stdin`**；**禁止**依赖 `stdout` 传数据（非 `PIME_MSG|` 行会被丢弃，`backend_manager.rs:335-337`）。
4. **禁止用异常表达"稍后再说"**：异常 = `success:false`，客户端视为失败（§3.5 实测）。
5. 需要长耗时：用 `threading.Thread` / `ThreadPoolExecutor` 放到后台，handler **立刻返回**；结果只能通过**后续请求**带出（`currentReply` 每请求清空，`textService.py:135-136`），或用 §3.6 的"加载中吃键 + showMessage"。
6. 后台线程 **不要**直接写 `self.currentReply`（属于别的请求，且随时会被清空）；只写实例字段。
7. 只要还在回包，就不会触发 15 s 看门狗（`protocol.rs:11`）；但**任何请求拖到 15 s** 都会导致后端被杀重启（`backend_manager.rs:441-461`），全部状态丢失。

**提交与组合**
8. 提交用 `setCommitString(str)` **一次性提交整串**；长度不受限（TSF 单次 `Range::SetText`，`libIME2 TextService.cpp:274`）。**不要**为了"逐字上屏"在一次按键里循环提交/`sleep`。
9. 需要"正在输入"的观感：用 `setCompositionString` + `setCompositionCursor` 维护组合区，最终 `setCommitString` 收尾；空 `compositionString` 且 `showCandidates=false` 会结束组合（`PIMEClient.cpp:177-232`）。
10. `showMessage(msg, duration)` 的 `duration` 单位是**秒**（`textService.py:275-279`），C++ 直接透传（`PIMEClient.cpp:135-144`）。
11. `setKeyboardOpen` 的回复键名是 **`openKeyboard`**（`textService.py:255-256`），写错名字等于没写（C++ 只读 `openKeyboard`，`PIMEClient.cpp:310`）。
12. `seqNum` 不要自己造：直接用 `msg["seqNum"]` 的回显即可（`server.py`/`textService.py` 已处理），自造会导致 C++ 校验失败（`PIMEClient.cpp:759-762`）。

**进程与依赖**
13. **只能用标准库 + 内置 `tornado`**（`requirements.txt` 只有 `tornado>=6.0`；无 pip、无编译扩展能力）。假设 cips 后端要 **stdlib-only**：`ctypes/threading/queue/json/sqlite3/ssl/zlib` 均已实测可用。
14. **32 位进程**：`ctypes` 结构体、句柄、`size_t` 全按 4 字节；不能加载 64 位 DLL（§5.4）。
15. 脚本目录**不会**自动进 `sys.path`（实测）⇒ 多文件后端必须自己 `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`；且注意 `$INSTDIR\python` 在 `sys.path` 里且**顺序可能在我们之前**，不要用与上游同名的模块名（如 `textService`、`input_methods`）。
16. 运行时可写目录只有 `%LOCALAPPDATA%`（安装目录在 Program Files，需要管理员）；模型/缓存/日志都写 `%LOCALAPPDATA%\cips\` 之类。
17. 后端进程会被 Launcher 按需 spawn、`/quit` 或崩溃后重启；**不要假设进程常驻**，状态要能重建（或用磁盘缓存加速重建）。
18. 卸载/注销会按 CLSID 清 `User Profile` 下所有 PIME 输入法条目（`libIME2 ImeModule.cpp:385-437`）⇒ 我们的 ime.json 会一起消失，这是预期行为。

**部署**
19. 目录必须是 `<Program Files (x86)>\PIME\python\input_methods\<name>\`，`ime.json` 的 `name` 非空、`guid` 合法且唯一、`moduleName`/`serviceName` 与代码一致（`DllEntry.cpp:94-103`、`serviceManager.py:72-75`）。
20. **新增输入法后必须重跑 `regsvr32`（System32 + SysWOW64 各一次，管理员）并重启 PIMELauncher**（§4.3）；仅重启 Launcher 不够。
21. 不修改 `_pime/` 是可行的：所有定制都在 `input_methods\<name>\` 内 + 一次 regsvr32。**唯一需要动上游的场景**是改超时/改协议（本机无 VS2026/Rust，做不到）。

---

## 8. 未确认事项

- `libIME2` 的 `ImeModule::registerServer` 在 **ARM64X** 下的注册细节（本地 submodule 为空，仅按 `78e6104` 的 x86/x64 分支代码推断）；`PIMETextService/CMakeLists.txt:34-46` 显示 ARM64 会链接成 ARM64X forwarder。
- `ITfInputProcessorProfiles::AddLanguageProfile` 在 Win11 24H2+ 上的语言列表呈现是否仍依赖 `User Profile` 手工写入（代码两条路都走，实测环境为 Win11，未做全版本矩阵验证）。
- PIME 自带的语言栏（`PIMELangBarButton`）在 Win11 新 IME 框架下的显示行为，未实测。
- `icon.ico` 的具体尺寸/格式要求（`IconCache` 有缓存逻辑，但未逐一验证尺寸约束）。
- beta4 与 1.3.0-stable 的 `ime.json`/协议差异（本文只对 2.0.0-beta4 取证；1.3.0 的 `server.py` 未比对）。
