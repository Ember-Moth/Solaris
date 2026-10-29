# 架构

本文记录 mihomo 桌面客户端（仓库 `Ember-Moth/mihomo-desktop`）的目标架构。项目最初计划基于 sing-box，
后来因为官方 sing-box for Desktop 已经发布，改为基于 mihomo。产品名尚未确定，下文用 `<Product>`（显示名）和
`<product>`（服务名、管道名、bundle id 等标识符）占位。代码中暂用工作名 `mihomo-desktop`，
集中在 `src/core/internal/product/product.go` 与 `src/mihomo_desktop/product.py`；协议草案见
`proto/mihomo_desktop/protocol/control.proto`。

状态：设计阶段，尚无代码。标注"待定"的内容见 [第 12 节](#12-待定事项)。

## 1. 定位与原则

- 面向 Windows 与 macOS 的 mihomo 图形客户端，界面使用 PySide6，并以 Nuitka standalone 模式打包。
- **内核以 Go 库形式内嵌，不作为 CLI 子进程运行。** Windows 上内核位于自有的 LocalSystem 服务内，
  macOS 上位于 NetworkExtension 系统扩展内。两端共用本仓库自己实现的 Go 内核层 engine，直接依赖 mihomo；
  [libclash](https://github.com/Herta-Network/libclash) 只作参考，不作为依赖（见第 3 节）。
- **UI 始终以普通用户权限运行。** Windows 只在安装器处请求一次 UAC；macOS 只在首次激活系统扩展时需要用户批准。
- **关闭 UI 后隧道继续运行。** Windows 服务按属主持久化配置，并在开机后恢复；macOS 的 NE 本身就独立于 App 运行。
- **控制面是自定义的方法调用协议，不使用 HTTP。** Windows 上走命名管道；macOS 上走 Network Extension
  自带的 IPC（provider message），不监听任何 socket。两端都不开放 TCP 端口，mihomo 自带的 HTTP
  控制器（Clash API）在任何情况下都不启动。

### 为什么不以子进程运行 mihomo

现有客户端普遍采用"特权服务拉起 mihomo 可执行文件"的方式。例如 Clash Verge Rev 的
`clash-verge-service-ipc` 以 `-d … -f … -ext-ctl-pipe …` 启动内核。这种方式有三个问题：

- 服务必须接受"启动哪个程序、带什么参数"之类的输入，这是一个提权攻击面；
- 需要同时监管两个进程；
- 内核的控制器由内核自己按配置开放，服务无法插手。

内嵌之后，配置准入、控制接口和危险功能的关闭都由服务在同一进程内强制执行。

### 为什么不用 Clash API

继续用 Clash API，就得启动 mihomo 的 HTTP 控制器，再配一条内部管道、一个反向代理、一份路径白名单和
secret 注入。而且接口面由 mihomo 决定：上游升级时新增的接口要逐个审查，还得靠 embed mode 关掉
`/restart`、`/upgrade` 这类危险接口。

改为自有的方法协议后，对外只有方法表里列出的方法，每个方法直接调用 engine。接口面完全由
本项目控制，参数和返回值都有类型。

## 2. 组件与进程边界

```text
                       PySide6 UI  (user, unprivileged)
               control client: Protobuf Request / Response
                 |                                        |
   Windows       | named pipe, framed             macOS   | ctypes -> Swift manager dylib
                 v                                        |   activate sysext, start/stop tunnel,
   <product>-service.exe  (LocalSystem, Go)               |   sendProviderMessage(Request)
     \\.\pipe\ProtectedPrefix\Administrators\<product>    v
     peer check, host + core methods             <product>.systemextension  (Swift)
     engine (mihomo), owned-TUN mode               handleAppMessage -> Go dispatcher
     no mihomo HTTP controller                     engine (mihomo), fd mode
                                                   no socket, no HTTP controller
```

| 组件 | 语言 | 权限 | 职责 |
| --- | --- | --- | --- |
| UI | Python / PySide6 | 用户 | 界面、配置与订阅管理、调用平台宿主 |
| engine | Go | 随宿主 | 直接内嵌 mihomo：配置准入、生命周期、查询 |
| 控制分发器 | Go | 随宿主 | 解码 Request、调用方法、编码 Response、维护订阅缓冲；Windows 服务与 macOS 扩展共用 |
| Windows 服务 | Go | LocalSystem | 管道、帧编解码与身份校验、属主、持久化与恢复、系统代理 |
| NE 管理库 | Swift（C ABI） | 用户 | 系统扩展激活、隧道配置与启停、转发控制消息 |
| NE 系统扩展 | Swift + Go | root（系统扩展） | 隧道网络设置、utun fd、运行内核、把 app message 交给分发器 |

本仓库的 Go 模块（`src/core/`）包含 engine、控制分发器和 Windows 服务；macOS 用的 xcframework
也由这个模块编出，其中包含 engine 和控制分发器。

## 3. Go 内核层：engine

内核层由本仓库自己实现，位于 `src/core/internal/engine`，直接依赖 mihomo（`github.com/metacubex/mihomo`）。

[libclash](https://github.com/Herta-Network/libclash) 是此前为移动端写的 CMFA 风格封装，**只作参考，不作为依赖**。
可以借鉴的有：配置补丁流水线的组织方式、fd 模式下启动 TUN 的写法、代理组与 provider 查询的实现。
它面向移动端的一些假设在桌面上不成立，对应的要求列在 3.3 节。

### 3.1 TUN 模式

| 模式 | 使用场景 | 路由 | TUN 来源 |
| --- | --- | --- | --- |
| fd 模式 | macOS NE | 宿主负责，`auto-route: false` | 宿主传入 fd（`tun.file-descriptor`） |
| 自管模式 | Windows 服务；CI 与测试虚拟机里的 TUN 测试 | mihomo 负责，开启 `auto-route`、`auto-detect-interface`、`strict-route` | mihomo 自建 wintun 网卡或 utun |
| 无 TUN 模式 | 本机开发 | 不改路由 | 不创建 TUN，只开放回环地址上的混合端口 |

配置流水线按模式参数化：fd 模式强制关闭配置里的 TUN 和自动路由，由宿主提供；自管模式按设置生成 TUN 段；
无 TUN 模式无视用户配置和覆写，强制关闭 TUN，并把监听限制在回环地址（见第 11 节）。

### 3.2 engine 的职责

- **初始化**：为当前属主设置 mihomo 的 home 目录（`C.SetHomeDir`），开启 `route.SetEmbedMode(true)`。
- **配置准入**：解析用户 YAML → 应用覆写 → 平台补丁（TUN 模式、DNS 默认值等）→ 最后一步强制安全项
  → `config.ParseRawConfig` → `hub.ApplyConfig`。`Validate` 只走到解析为止。
- **启停与重载**：Start 首次应用配置；Reload 原地 `ApplyConfig`，TUN 配置不变时不重建 TUN；Stop 调用
  `executor.Shutdown`。
- **查询与操作**：模式、代理组与节点、选择节点、测速、provider、规则、连接列表与关闭、流量与内存、
  fake-ip 与 DNS 缓存清理、日志订阅，覆盖 4.3 节的内核方法表。
- **单实例**：mihomo 用包级全局变量保存状态，一个进程只能有一个 engine。

### 3.3 以 SYSTEM 身份运行时的安全要求

Windows 服务以 SYSTEM 身份运行内核，下面每一项都必须满足，并且各有回归测试（例如"用户配置里的
`external-controller-pipe` 一定会被清除"）。这些要求源自 libclash 里在移动端由应用沙箱兜底、在桌面上
不再成立的地方。

| # | 要求 | 原因 |
| --- | --- | --- |
| 1 | 任何构建都不带 `cmfa` 标签 | 该标签会让 mihomo 的 `IsSafePath` 恒为 true，路径沙箱失效 |
| 2 | 流水线最后一步（在覆写之后）清空 `external-controller`、`-tls`、`-unix`、`-pipe`、`external-doh-server` 和 `secret` | 否则用户配置或覆写可以让 SYSTEM 开出 mihomo 控制器；其中 `-pipe` 的默认 SDDL 允许所有本地用户读写 |
| 3 | 初始化时开启 `route.SetEmbedMode(true)` | 第二道防线：万一控制器被启动，也关闭 `/restart`、`/configs` 写入和 `/upgrade` |
| 4 | provider 路径用 `filepath` 规范化并检查 `filepath.IsLocal`；不设置 `SKIP_SAFE_PATH_CHECK` / `SAFE_PATHS` | 只按 `/` 切分时，Windows 上的 `..\` 可以跳出 `providers/`；`IsSafePath` 作为兜底 |
| 5 | 持久化节点选择（`profile.store-selected`） | 开机恢复后要回到用户上次选的节点 |

### 3.4 构建标签

mihomo 提供的可选标签有 `with_gvisor`、`with_low_memory`、`no_tailscale`、`no_zerotier`、
`no_easytier`、`no_fake_tcp`。Windows 服务和 macOS NE 各用哪个组合，等 TUN 栈和体积实测后再定；
任何情况下都不带 `cmfa`。

mihomo 的 GitHub 默认分支 `main` 是伪装项目，真正的代码在 `Alpha`（开发）和 `Meta`（稳定）分支。
发布构建钉在 `Meta` 的 tag 上（撰写时为 v1.19.33）。

## 4. 控制协议

两个平台共用同一组 Protobuf 消息和同一个 Go 分发器，区别只在传输层和"启停"这类宿主方法。
协议只有请求和响应两种消息，没有服务端主动推送，因为 macOS 的 provider message 只支持一问一答。
schema 放在 `proto/<product>/control/v1/control.proto`，Go 与 Python 的绑定由它生成。
Swift 侧（管理库和扩展）只转发字节，不解析消息。

### 4.1 消息

```proto
message Request  { uint32 protocol_version = 1; uint64 id = 2; oneof method { ... } }
message Response { uint32 protocol_version = 1; uint64 id = 2; oneof result { Error error = 3; ... } }
message Error    { string code = 1; string message = 2; bool retryable = 3; }
```

- 协议版本放在消息体里，两种传输都能校验；版本不一致时返回错误，不做兼容。
- 响应的 `id` 必须与请求一致。
- 未知方法、未知枚举、缺少必填字段一律返回错误，不做兼容猜测。
- 分发器内部，会改变状态的方法（认领、启动、重载、停止、切换模式等）由同一把操作锁串行执行；
  查询方法可以并发。

### 4.2 传输

| | Windows | macOS |
| --- | --- | --- |
| 通道 | 命名管道（见 5.3 节） | `NETunnelProviderSession.sendProviderMessage` → `handleAppMessage`（见 6.3 节） |
| 分界 | 12 字节帧头 + 载荷 | 系统按整条消息投递，不需要帧头 |
| 连接 | 每个 UI 进程一条长连接，可同时有多个请求在途 | 每次调用一问一答 |
| 身份 | 连接建立时校验一次 | 由系统保证只有本应用能取得 session |
| 可用时机 | 服务运行即可 | 隧道已连接、扩展进程在运行时 |

Windows 的帧头为 12 字节，整数一律小端序：

| 字段 | 长度 | 说明 |
| --- | --- | --- |
| magic | 4 字节 | 随产品名确定 |
| version | u16 | 协议版本，与服务端不一致时直接断开 |
| kind | u16 | 1 请求，2 响应 |
| length | u32 | 载荷长度。请求上限 16 MiB（配置可能很大），响应上限 8 MiB |

服务端先校验帧头（magic、版本、类型、长度上限），再分配载荷缓冲区。macOS 上由扩展在解码前检查消息长度。
Windows 上 UI 断线后按退避间隔重连，并重新建立订阅。

### 4.3 方法表

宿主方法负责生命周期和属主，只在 Windows 服务上提供。macOS 的启停走 `NETunnelProviderManager`。

| 方法 | 平台 | 说明 |
| --- | --- | --- |
| `GetInfo` | 两者 | 协议版本、服务版本、mihomo 版本、运行状态；Windows 上还返回属主状态（空闲 / 调用者 / 其他用户） |
| `Claim` | Windows | 服务空闲或调用者已是属主时认领 |
| `Validate` | Windows | 配置只经过流水线解析，不应用 |
| `Start` | Windows | 提交配置 YAML、覆写和运行选项，然后准入、应用、持久化 |
| `Reload` | 两者 | 运行中替换配置，由 `hub.ApplyConfig` 原地应用 |
| `Stop` | Windows | 停止内核并清除 `was_running` |
| `SetSystemProxy` | Windows | 开启或关闭属主的系统代理 |

内核方法两个平台都有，直接调用 engine：

| 方法 | 说明 |
| --- | --- |
| `GetStatus` | 运行状态、模式、运行时长、内存用量、连接数 |
| `SetMode` | 切换 rule / global / direct |
| `ListGroups` / `GetGroup` | 代理组及节点，支持排序 |
| `SelectProxy` | 在 selector 组中选择节点 |
| `HealthCheck` / `TestDelay` | 组健康检查、单节点测速 |
| `ListProviders` / `UpdateProvider` | provider 列表与更新 |
| `ListRules` | 规则列表 |
| `CloseConnection` / `CloseAllConnections` | 关闭连接 |
| `FlushFakeIP` / `FlushDNSCache` | 清理 fake-ip 池和 DNS 缓存 |
| `SubscribeLogs` / `SubscribeTraffic` / `SubscribeConnections` | 开始订阅，返回 subscription_id |
| `PollEvents` | 取走某个订阅自上次拉取以来缓存的事件 |
| `Unsubscribe` | 结束订阅 |

### 4.4 订阅

协议没有服务端推送，订阅由"服务端缓存 + 客户端拉取"实现：

- 订阅建立后，分发器为它维护一个有界缓冲区，持续写入事件。
- 客户端定期调用 `PollEvents` 取走缓冲区里的事件。
- 缓冲区满时丢弃最旧的事件，下一次 `PollEvents` 的结果里带上丢弃数量。单个慢客户端不会阻塞内核。
- 超过 30 秒没有被拉取的订阅自动失效。Windows 上连接断开时，该连接的订阅也立即失效。

| 订阅 | 事件 | 建议拉取间隔 |
| --- | --- | --- |
| 日志 | 每条日志，按级别过滤 | 0.5 秒 |
| 流量 | 每秒一条：上下行速率与累计值 | 1 秒 |
| 连接 | 每秒一份快照 | 1 秒 |

两个平台用同一套拉取逻辑，UI 不需要为推送和拉取分别写代码。

## 5. Windows：特权服务

### 5.1 安装布局

```text
C:\Program Files\<Product>\            管理员所有，用户只读
├── <product>.exe                      Nuitka standalone UI
├── <product>-service.exe              Go 服务
└── …                                  Python / Qt 运行依赖

C:\ProgramData\<Product>\              仅 SYSTEM 与 Administrators 可访问
├── service.log
├── owner.json                         当前属主 SID、会话
└── users\<SID>\                       该用户的 mihomo home 目录（IsSafePath 根）
    ├── config.yaml                    UI 提交的配置
    ├── override.json                  持久覆写
    ├── options.json                   运行选项与 was_running
    ├── providers\ …                   provider 与规则集缓存
    └── cache.db                       mihomo profile 缓存
```

UI 必须使用 Nuitka standalone 模式。onefile 模式会解压到 `%TEMP%` 后运行，客户端路径校验会失败，
而且 `%TEMP%` 对用户可写。

### 5.2 服务

- 服务名 `<Product>Service`，以 LocalSystem 身份运行，自动启动。
- SCM 故障恢复：依次在 5 s、15 s、60 s 后重启。
- 接受的控制事件：Stop、Shutdown、SessionChange、PowerEvent。PowerEvent 用于唤醒后重置连接。
- 启动时读取 `owner.json` 和对应用户的 `options.json`；若 `was_running` 为真，按保存的配置恢复隧道。
- 超时逐层递减，参考 hypervpn：UI 请求超时 > 服务处理超时 > 内核启动或停止超时。
  这样内核卡死时，服务的操作锁和 SCM 的停止流程不会被一并卡住。

### 5.3 管道与客户端校验

管道名为 `\\.\pipe\ProtectedPrefix\Administrators\<product>`，SDDL 为
`D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)`。这是服务唯一的对外入口。

`ProtectedPrefix\Administrators` 前缀下只有管理员和 SYSTEM 能创建管道，因此客户端不必再核对服务端
PID 来防止管道名被抢注。

服务在每条连接建立时校验客户端，任一项不通过就断开：

1. 用 `GetNamedPipeClientProcessId` 取得客户端进程，并在整个连接期间持有其进程句柄；
2. 客户端映像的最终路径必须是安装目录里的 `<product>.exe`，并锁住文件句柄、比较文件 ID，防止中途被替换；
3. 读取客户端 token 的用户 SID 和会话 ID，作为后续属主判断的身份；
4. 发布构建额外要求客户端的 Authenticode 签名者与服务相同。

后三项的做法参考 sing-box `experimental/boxdd` 的 `peer_windows.go`。

### 5.4 属主

- 同一时间只有一个属主（用户 SID 加会话）。
- 属主以外的用户只能调用 `GetInfo`，其余方法一律返回 `owned_by_other_user` 错误。
- 内核运行中如何移交给其他用户，待定。

### 5.5 系统代理

系统代理是每个用户 HKCU 下的设置，服务以 SYSTEM 身份直接写只会改到 SYSTEM 自己。
服务的做法是：用 `WTSQueryUserToken` 取得属主会话的 token，模拟该用户写入 Internet Settings 并通知
WinINet；内核停止时还原。开机恢复时，如果属主尚未登录，就等收到 SessionLogon 事件再写入。
实现参考 boxdd 的 `platform_windows.go`。

## 6. macOS：NetworkExtension

### 6.1 包结构

```text
<Product>.app/Contents/
├── MacOS/<product>                                   Nuitka standalone UI
├── Frameworks/<Product>Manager.dylib                 Swift，C ABI，供 ctypes 调用
└── Library/SystemExtensions/<bundle-id>.systemextension
    └── NEPacketTunnelProvider + 本仓库 Go 模块编出的 xcframework
```

- 需要 Developer ID 签名、公证，以及 `packet-tunnel-provider-systemextension` entitlement。
- 管理库负责三件事：用 `OSSystemExtensionRequest` 激活扩展（用户需在系统设置中批准）、配置
  `NETunnelProviderManager`、启停隧道。
- hypervpn-desktop 的 `src/packet-engine/native/macos/SystemNetworkExtension/` 和它的 NE 管理库可以作为参考。

### 6.2 扩展内的运行流程

1. 用 `setTunnelNetworkSettings` 设置路由、地址和 DNS，DNS 指向 mihomo 的 dns-hijack 地址；
2. 找到 utun 的 fd，以 fd 模式启动 engine；
3. 加载配置，初始化 Go 分发器。不监听任何 socket，mihomo 的 HTTP 控制器也不启动。

扩展自身发出的流量本来就不进入隧道，因此不用设置 socket 保护回调。

### 6.3 控制通道

macOS 不监听任何 socket，控制消息全部走 Network Extension 自带的 IPC：

1. UI 通过 ctypes 调用管理库的发送函数，传入编码好的 Request；
2. 管理库调用 `NETunnelProviderSession.sendProviderMessage` 发给扩展，超时则返回错误；
3. 扩展在 `handleAppMessage` 里把消息交给 Go 分发器（xcframework 导出的 C 函数）。
   分发在后台队列上执行，不阻塞 provider 的其他回调；
4. 分发器返回编码好的 Response，经 completion handler 回到管理库，再交回 Python。

- **访问控制由系统负责**：只有本应用（同一 Team ID、带 NE entitlement 的签名进程）能加载自己的
  `NETunnelProviderManager` 并取得 session，扩展内不再做对端校验。
- **只有隧道已连接时可用**：扩展进程只在隧道连接期间运行。未连接时，UI 只能通过管理库查看连接状态。
- **方法与 Windows 相同，但没有宿主方法**：启停走管理库；`GetInfo`、`Reload` 和内核方法走 provider message。
- **消息大小**：单条 provider message 的上限需要实测，它决定 `Reload` 能否直接携带大配置。
- 做法与 hypervpn 一致，见它的 `NetworkExtensionManager.swift`（`providerExchange`）和
  `PacketTunnelProvider.swift`（`handleAppMessage`）。

## 7. Python UI

- 分层参考 hypervpn：视图 → ViewModel → models → 宿主客户端。UI 用 QML 还是 Widgets 待定。
- asyncio 事件循环运行在后台线程，结果通过排队信号回到 Qt 线程。
- **控制客户端**：负责构造 Request、校验 Response 的版本和 ID，并按间隔调用 `PollEvents` 拉取订阅。
  传输有两种实现：
  - Windows：一条长连接（`ProactorEventLoop.create_pipe_connection`），负责帧编解码、按请求 ID
    匹配响应和断线重连；
  - macOS：每个请求通过 ctypes 调用一次管理库。这是阻塞调用，放到线程池里执行；
  - 依赖只有 protobuf，不需要 HTTP 或 gRPC 库。
- **宿主抽象** `TunnelHost` 有两个实现，共用同一个控制客户端：
  - `WindowsServiceHost`：全部方法都走命名管道；
  - `MacExtensionHost`：启停和控制消息都通过 ctypes 调用管理库。
- **订阅**在用户上下文中下载，存放在 `%LOCALAPPDATA%\<Product>\` 或 `~/Library/Application Support/<Product>/`。
  UI 把最终的 YAML 和覆写交给宿主。provider 和规则集的后续更新仍由 mihomo 自己完成。

## 8. 安全模型

| 威胁 | 措施 |
| --- | --- |
| 其他本地进程冒充 UI | Windows：连接建立时校验映像路径、文件身份、SID 与会话，发布构建再校验签名者；macOS：只有本应用能取得 NE session |
| 抢注服务管道名 | 使用 `ProtectedPrefix\Administrators` 前缀 |
| 配置让 SYSTEM 读写任意文件 | 每个 SID 独立的 mihomo home 目录 + `IsSafePath`（不带 `cmfa`，不设 `SKIP_SAFE_PATH_CHECK` / `SAFE_PATHS`），并修正 `ResolveAsRoot` |
| 配置开放 mihomo 控制器 | engine 清空全部控制器字段，HTTP 控制器从不启动；embed mode 作为第二道防线 |
| 通过控制接口替换二进制或重启进程 | 对外只有方法表中的方法，没有对应能力 |
| 畸形或超大的消息 | Windows 先校验帧头再分配缓冲区，macOS 在解码前检查长度；未知方法和枚举直接拒绝 |
| 订阅拖住内核或泄漏 | 事件缓存在有界缓冲区，满了丢弃最旧的；超过 30 秒未拉取的订阅自动失效 |
| 其他用户接管隧道 | 属主 SID 校验 |
| 局域网暴露 | `allow-lan` 和监听 `0.0.0.0` 的 listeners 默认关闭，由用户显式开启 |
| UI 与内核侧版本不一致 | 消息体（Windows 上还有帧头）携带协议版本，不一致时拒绝；Windows 安装器同时更新 UI 与服务，macOS 的 UI 与扩展在同一个包里 |

## 9. 安装、升级与卸载

- **Windows**：使用 NSIS 安装器，它是唯一的 UAC 点。
  - 安装：写入程序文件、注册服务和故障恢复策略、注册 URL scheme。
  - 升级：先请运行中的 UI 退出（维护退出），再停止服务、替换文件、启动服务。
  - 卸载：停止并删除服务；保留用户的订阅与偏好，不递归删除未知文件。
  - 流程参考 hypervpn 的 `packaging/windows/`。
- **macOS**：拖入"应用程序"文件夹完成安装，首次连接时激活系统扩展。卸载时先停用扩展。

## 10. 构建与发布

| 产物 | 构建方式 |
| --- | --- |
| Windows 服务 | `go build`，按 3.4 节的标签，用 `-ldflags -X` 注入版本 |
| macOS xcframework | 本仓库 Go 模块的 c-archive 构建，包含 engine 与控制分发器，导出供 `handleAppMessage` 调用的 C 函数 |
| 协议绑定 | 由 `control.proto` 生成 Go 与 Python 代码 |
| UI | Nuitka standalone |
| Windows 安装包 | NSIS |
| macOS 包 | `.app` 签名与公证 |

- 需要的证书：Windows Authenticode（发布构建）、Apple Developer ID。
- 许可：本项目以 GPL-3.0-only 发布（[LICENSE](../LICENSE)）；服务和扩展链接的 mihomo 同为 GPL-3.0。
  发布二进制时须同时提供对应的源代码；新增依赖须与 GPL-3.0 兼容。

## 11. 开发与测试

- **在 Mac 上调试 Windows 服务的逻辑**：服务二进制在非 Windows 平台可以作为前台进程运行，以普通用户身份
  启动；以 root 身份启动时直接拒绝运行。此时帧协议跑在一个本地 socket 上，客户端校验降级为 uid 检查，engine 使用无 TUN
  模式。这样 `ServiceHost` 和全部方法都能在 Mac 上联调。这只是开发模式，不进入任何发布包；macOS 正式版
  走 provider message，不使用 socket。
- **本机开发不得改动本机网络**：开发机就是日常使用的机器，开发模式必须满足以下各条，并有测试覆盖：
  - 不创建 TUN；用户配置或覆写里的 `tun`、`auto-route`、`auto-redirect`、`tproxy-port`、`redir-port` 一律被清除；
  - 混合端口和其他入站只监听 `127.0.0.1`，强制关闭 `allow-lan`；
  - 不设置系统代理，不修改路由和系统 DNS；
  - 请求开启 TUN 或系统代理的方法返回 `unsupported_method`。

  验证真实流量时，用 `curl -x http://127.0.0.1:<混合端口>` 走代理。
- **TUN 只在隔离环境里测试**：自管模式在 CI 的 Linux runner 上、独立的网络命名空间里测试；wintun 和
  strict-route 在 Windows 测试虚拟机里验证；macOS NE 在单独的测试 Mac 或虚拟机上验证，不在开发机上运行。
- **测试覆盖**：
  - engine：配置流水线的回归测试，对应 3.3 节各项；
  - 控制协议：帧头校验、超限拒绝、请求 ID 匹配、订阅缓冲与过期，Go 与 Python 两端用同一组样本互测；
  - Windows CI：服务的客户端校验、管道 SDDL、属主与恢复流程；
  - Python：用模拟宿主测试 models 与 ViewModel。
- **需要真机验收**：真实 TUN 流量、系统代理模拟写入、安装与升级、系统扩展激活。

## 12. 待定事项

- 产品名。它决定服务名、管道名、协议 magic、bundle id、安装目录和 URL scheme。
- 支持的架构：Windows x64 / arm64，macOS arm64 / x86_64。
- UI 技术：QML 还是 Widgets。
- 配置覆写能力的范围：是否支持类似 Clash Verge 的 Merge / Script。
- Windows 上属主注销时是否停止隧道；管理员能否接管其他用户的隧道。
- Windows 上的 TUN 栈选择（system / gvisor / mixed），以及是否需要 `with_gvisor`。
- macOS 上配置怎么传给扩展（`startVPNTunnel(options:)`，或者启动后用 `Reload` 传入）；
  隧道未运行时怎么校验配置。
- provider message 的单条大小上限，它决定 `Reload` 能否直接携带大配置。

## 13. 参考

| 来源 | 参考内容 |
| --- | --- |
| hypervpn-desktop `src/packet-engine/src/bin/windows_service/main.rs` | 管道 SDDL、客户端校验、属主与会话、超时分层、恢复记录 |
| hypervpn-desktop `proto/hyper_vpn/protocol/tunnel_control.proto`、`src/hyper_vpn/host/runtime/transport/windows.py` | 帧头格式、请求与响应匹配、版本拒绝 |
| hypervpn-desktop `packaging/windows/` | 安装、升级与卸载流程 |
| hypervpn-desktop `src/packet-engine/native/macos/SystemNetworkExtension/` | 系统扩展与 NE 管理；provider message 收发见 `NetworkExtensionManager.swift` 的 `providerExchange` 与 `PacketTunnelProvider.swift` 的 `handleAppMessage` |
| sing-box `experimental/boxdd`（testing 分支 `e5d91fa`） | `ProtectedPrefix` 管道、`peer_windows.go` 客户端校验、`platform_windows.go` 系统代理模拟 |
| clash-verge-service-ipc | 子进程模式的对照实现 |
| mihomo（`Alpha` 分支 `ca8d7c4`） | `hub`、`hub/executor`、`hub/route`（embed mode）、`constant/path.go`（IsSafePath） |
| libclash `02c0ab7`（仅参考） | 配置补丁流水线、fd 模式启动 TUN、代理组与 provider 查询的写法；3.3 节各项要求的来源 |
