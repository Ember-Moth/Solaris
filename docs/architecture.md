# 架构

本文记录 Solaris 的目标架构。Solaris 是 mihomo 的桌面客户端（仓库 `Ember-Moth/Solaris`），项目最初计划基于
sing-box，后来因为官方 sing-box for Desktop 已经发布，改为基于 mihomo。产品名、应用名都是 Solaris（决策 D1）；
代码里的产品标识集中在 `src/core/internal/product/product.go` 与 `src/solaris/product.py`，协议见
`proto/solaris/protocol/control.proto`。

本文只讲系统层面的结构；界面各部分的设计见 [ui.md](ui.md)，计划中的存储与分流见 [storage.md](storage.md) 和
[routing.md](routing.md)，进度、计划与决策见 [roadmap.md](roadmap.md)。

状态：engine、控制协议和开发模式服务已实现；Windows 服务的属主、系统代理和安装，以及 macOS 扩展尚未实现
（roadmap.md 的 M4、M5）。

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
   solaris-service.exe  (LocalSystem, Go)               |   sendProviderMessage(Request)
     \\.\pipe\ProtectedPrefix\Administrators\solaris    v
     peer check, host + core methods             dev.tschen.solaris.tunnel.systemextension  (Swift)
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

模式在创建 engine 时固定（`engine.TunNone`、`TunOwned`、`TunFD`），配置流水线按模式打补丁：

- **无 TUN 模式**无视用户配置和覆写：写入全部关闭的 `tun` 段（`enable`、`auto-route`、`auto-redirect`、
  `auto-detect-interface`、`strict-route`），清零 `redir-port` 和 `tproxy-port`，并把全部入站限制在回环地址
  （见第 11 节）。`RunOptions` 请求 TUN 时返回 `ErrUnsupported`。
- **自管模式**按 `RunOptions.tun_enabled` 写 `tun.enable`，强制 `auto-route`、`auto-detect-interface`、
  `strict-route` 为 true，`auto-redirect` 为 false，`file-descriptor` 为 0；保留用户的栈、设备名、MTU 和排除项。
  TUN 打开时同时打开 mihomo DNS，因为 dns-hijack 要交给它解析。`allow_lan` 为 false 时，入站同样限制在回环地址。
- **fd 模式**的接口已经留好，准入时返回"M5 实现"的 `ErrUnsupported`。届时宿主传入 fd，并关闭自动路由。
- 三种模式都会从 `listeners` 里删掉 `tun`、`redir`、`tproxy` 类型的入站：TUN 只能由 `tun` 段描述。
- "限制在回环地址"包括：`allow-lan: false`、`bind-address: 127.0.0.1`；`listeners` 的 `listen`、`tunnels` 的
  监听地址、`dns.listen`、`tuic-server.listen`、`ss-config` 和 `vmess-config` 的地址都改为 `127.0.0.1`，端口不变。
- `RunOptions.mixed_port` 非 0 时替换配置里的 `mixed-port`。

### 3.2 engine 的职责

- **初始化**（`engine.New`）：为属主设置 mihomo 的 home 目录（`C.SetHomeDir`），开启 `route.SetEmbedMode(true)`，
  然后自检路径沙箱。开发服务的 home 默认在用户配置目录下的 `Solaris/dev-core`（`-home` 可改）；
  Windows 服务暂用 `%ProgramData%\Solaris\core`，按 SID 划分的目录随 M4 的 `Claim` 实现。
  mihomo 在进程内只打开一次 `cache.db`，所以 home 在进程生命周期内不能更换，更换属主需要新的服务进程。
- **配置准入**：用户 YAML 解析为映射（锚点和合并键在这一步展开，补丁无法被别名绕过），或者由节点池和方案
  拼装出映射 → 依次应用覆写 → 按 TUN 模式打补丁 → 最后一步强制安全项 → 重新编码后交给
  `executor.ParseWithBytes` → 校验解析结果，任一项不符就拒绝（fail closed）→ `hub.ApplyConfig`。`Validate`、
  `Start`、`Reload` 都走同一条流水线，`Validate` 走到校验为止。
- **拼装**（决策 D16，[routing.md](routing.md) 第 8 节）：`Profile.Composition` 取代 YAML 时，
  `internal/compose` 把每份订阅转成内联 proxy-provider，按方案生成内置组、地区组和自建组（成员在内核里算好，
  写成 `use` 加精确的 `filter`），把规则条目展开成规则，再加上基础段（`compose/base.yaml`：fake-ip DNS、嗅探、
  GEO 数据）。拼装只是流水线的起点：覆写、平台补丁和安全项照常在它之后，有测试守住这一顺序。`Validate`、
  `Start`、`Reload` 返回拼装报告（警告、生成的组和规则、节点池）；应用后 engine 保存"规则 → 规则条目"的对照表，
  `ListRules` 和连接快照用它带上条目 id，改用 YAML 时清空。
- **GEO 数据**（D17）：拼装的配置用 home 里的 `GeoSite.dat`、`GeoIP.dat`。mihomo 只在它自己的 `main` 里启动 GEO
  更新器，所以配置开启 `geo-auto-update` 时 engine 自己运行一个（每小时检查，数据过期就调用
  `updater.UpdateGeoDatabases`），停止内核时一同停止；新数据在下一次应用时生效。home 里缺数据时 mihomo 会在解析
  时同步下载，所以安装包附带一份（`scripts/fetch_geodata.py`），测试写入一份很小的数据，从不下载。
- **覆写**（决策 D4：只做 Merge，不做 Script）：
  - 来源是一份全局覆写加上每个配置各一份。请求里的 `overrides` 是一组 YAML 映射原文，按顺序应用：
    先全局覆写，再当前配置的覆写，后面的覆盖前面的。空串或只有注释的覆写不做任何事。
  - 位置在"解析用户 YAML"之后、平台补丁和 3.3 节安全项之前，所以覆写改不动开发模式护栏、自管模式的
    TUN 段和安全项（有测试覆盖）。
  - `prepend-rules` / `append-rules`、`prepend-proxies` / `append-proxies`、`prepend-proxy-groups` /
    `append-proxy-groups` 把列表插到 `rules`、`proxies`、`proxy-groups` 的前面或后面；插入发生在同一段覆写的
    其他键合并之后，所以同一段里替换整个列表再追加也成立。这六个键不进入最终配置。
  - 其余键做深度合并：映射递归合并，标量和列表整体替换。
  - 某段覆写解析失败（不是 YAML 映射，或插入键的值、目标不是列表）时返回 `invalid_config`，信息形如
    `override 2 of 3: …`，指出第几段出错。
  - mihomo 解析时会临时套用新配置的 general 部分（`temporaryUpdateGeneral`），所以运行中的 `Validate`
    会短暂影响模式等全局设置；分发器用操作锁串行执行它。
- **启停与重载**：
  - Start 首次应用配置；Reload 原地 `hub.ApplyConfig`，mihomo 在 TUN 配置不变时不重建 TUN。
  - Reload 不断开已有连接：`hub.ApplyConfig` 只替换代理表和规则表（`tunnel.UpdateProxies`、`UpdateRules`），
    不碰 `statistic.DefaultManager` 里的连接；入站只在地址变化时重建，关闭的也只是监听 socket。已有连接沿用
    建立时选定的出站，直到结束，即使那个节点已从新配置里删掉。应用期间（毫秒级）隧道处于 Suspend，这时新到的
    连接会被直接关闭（`tunnel.isHandle`）。A2 实测见 [routing.md](routing.md) 第 12 节。
  - 应用前先试开新配置要新开的入站端口（TCP，混合端口和 SOCKS 端口还有 UDP），已经由内核打开的跳过。端口被
    占用时直接返回 `ErrApplyFailed`，信息是系统的监听错误，什么都不改：Reload 保留原来的配置和端口，Start
    进入 FAILED。`hub.ApplyConfig` 会先关旧端口再开新端口、失败只记日志，没有这一步，运行中的内核会丢掉端口。
  - 应用后仍核对端口和 TUN 是否真的打开（`hub.ApplyConfig` 只记日志、不返回错误；试开与应用之间端口可能
    被抢走），没打开就返回 `ErrApplyFailed`：Start 回到空闲配置并进入 FAILED；Reload 保持运行并记录 `last_error`。
  - Stop 先调 `executor.Shutdown`（关闭 TUN、清理 iptables、保存 fake-ip 池），再应用一份空闲配置，
    然后关闭全部连接和旧 provider。`executor.Shutdown` 本是为进程退出写的，单独调用会留下监听端口、
    DNS 服务和 provider 的后台任务。对已停止的内核调用 Stop 不做任何事。
  - 连续启停 50 次后 goroutine 和文件描述符数回到初始水平（`TestStartStopDoesNotLeak`）。
- **查询与操作**：模式、代理组与节点、选择节点、测速、provider、规则、连接列表与关闭、流量与内存、
  fake-ip 与 DNS 缓存清理、日志订阅，覆盖 4.3 节的内核方法表。
  - 代理组按配置顺序（即 `GLOBAL` 的成员顺序）列出，`GLOBAL` 排最后，隐藏组不列出。
  - 选择节点对 selector 生效，对 url-test、fallback 是固定节点；健康检查会像 mihomo 自己的组测速一样解除固定。
  - 节点的 `delay_ms` 按所在组的测速地址取：从没测过时不填，最近一次测速失败时为 0，否则是延迟。界面把前者
    显示为"—"、后者显示为"超时"。重载会换掉节点对象，测速结果随之清空。
  - 连接的 `rule` 和规则列表的 `type` 用配置里的写法（`DST-PORT`、`MATCH`），不用 mihomo 内部的类型名
    （`DstPort`、`Match`）。
  - provider 列表不含 mihomo 为组内联节点生成的 compatible provider。
- **单实例**：mihomo 用包级全局变量保存状态，`engine.New` 在已有 engine 打开时直接失败。
- **mihomo 自身的竞态**：`log.SetLevel` 写全局变量时没有同步，`go test -race` 下 Reload 会报出；属于上游问题。

### 3.3 以 SYSTEM 身份运行时的安全要求

Windows 服务以 SYSTEM 身份运行内核，下面每一项都必须满足，并且各有回归测试（`internal/engine/security_test.go`
里的 `TestSecurity1` 到 `TestSecurity5`）。这些要求源自 libclash 里在移动端由应用沙箱兜底、在桌面上不再成立的地方。

| # | 要求 | 原因 | 实现与测试 |
| --- | --- | --- | --- |
| 1 | 任何构建都不带 `cmfa` 标签 | 该标签会让 mihomo 的 `IsSafePath` 恒为 true，路径沙箱失效 | `cmfa.go` 只在 cmfa 构建里参与编译，且无法通过类型检查；`New` 在 `features.CMFA` 为真时拒绝运行 |
| 2 | 流水线最后一步（在覆写之后）清空 `external-controller`、`-tls`、`-unix`、`-pipe`、`external-doh-server` 和 `secret` | 否则用户配置或覆写可以让 SYSTEM 开出 mihomo 控制器；其中 `-pipe` 的默认 SDDL 允许所有本地用户读写 | 同时清空 `external-ui`、`-url`、`-name`（否则 mihomo 会往 home 下载面板）；解析后再校验一次，各模式都测 |
| 3 | 初始化时开启 `route.SetEmbedMode(true)` | 第二道防线：万一控制器被启动，也关闭 `/restart`、`/configs` 写入和 `/upgrade` | 测试手动起一个 unix socket 控制器，确认这些接口被拒绝 |
| 4 | provider 路径用 `filepath` 规范化并检查 `filepath.IsLocal`；不设置 `SKIP_SAFE_PATH_CHECK` / `SAFE_PATHS` | 只按 `/` 切分时，Windows 上的 `..\` 可以跳出 `providers/`；`IsSafePath` 作为兜底 | mihomo 在启动时读取这两个变量，`New` 按行为自检（home 外的路径必须被拒），不通过就拒绝运行；测试用子进程带上变量验证 |
| 5 | 持久化节点选择（`profile.store-selected`） | 开机恢复后要回到用户上次选的节点 | 强制为 true；测试选择节点后重启内核，确认仍是该节点 |

流水线还在所有模式下强制两项：`iptables.enable: false`（仅 Linux，失败时 mihomo 会直接退出进程）和
`ntp.write-to-system: false`（修改系统时间需要用户未必拥有的权限，不能由 SYSTEM 代劳）。

### 3.4 构建标签

mihomo 提供的可选标签有 `with_gvisor`、`with_low_memory`、`no_tailscale`、`no_zerotier`、
`no_easytier`、`no_fake_tcp`。任何情况下都不带 `cmfa`。Windows 服务带 `with_gvisor` 构建，四种 TUN 栈都提供，由用户在设置里选择（决策 D3）；
macOS NE 的组合在 M5 确定。M1 的实测数据如下（v1.19.33，Go 1.27.3，`-trimpath -ldflags "-s -w"`，`./service` 的二进制体积）：

| 标签 | Windows amd64 | macOS arm64 |
| --- | --- | --- |
| （无） | 53.9 MiB | 49.7 MiB |
| `with_gvisor` | 67.6 MiB | 61.6 MiB |
| `with_gvisor,no_tailscale` | 57.7 MiB | 53.6 MiB |
| `no_easytier` | 43.3 MiB | 39.2 MiB |
| `no_zerotier` | 52.7 MiB | 48.7 MiB |
| `no_tailscale`、`no_fake_tcp`、`with_low_memory`（单独） | 不变 | 不变 |
| `no_tailscale,no_zerotier,no_easytier,no_fake_tcp` | 42.1 MiB | 38.2 MiB |
| 上一行加 `with_gvisor`（再加 `with_low_memory` 体积不变） | 46.0 MiB | 42.0 MiB |

- 体积大头是 easytier 出站（约 10.6 MiB）和 tailscale 出站（约 9.9 MiB）。tailscale 出站只在 `with_gvisor`
  构建里编入，所以单独加 `no_tailscale` 没有效果；gVisor 栈本身约 3.8 MiB。
- TUN 栈：`system` 和 `mips`（mihomo 的默认栈）总是可用；`gvisor` 和 `mixed` 需要 `with_gvisor`，否则要到创建
  TUN 时才报错，所以准入流水线在缺少该标签时直接以 `invalid_config` 拒绝这两种栈。
- macOS NE 开启 `includeAllNetworks` 时，sing-tun 拒绝 `system` 和 `mixed` 栈，只剩 `mips` 和 `gvisor`。
- 各标签组合在两个平台上都能编译。真实 TUN 下，四种栈在 Linux 的自管模式里都能建立 TUN 并转发流量
  （Lima 虚拟机内的网络命名空间实测，Ubuntu 26.04 aarch64，见第 11 节）；Windows 上的 wintun 待 M4 实测。

mihomo 的 GitHub 默认分支 `main` 是伪装项目，真正的代码在 `Alpha`（开发）和 `Meta`（稳定）分支。
发布构建钉在 `Meta` 的 tag 上，当前为 v1.19.33（即 `Meta` 的 `3f27ef3`）。mihomo 的 go.mod 把
`google.golang.org/protobuf` 替换为 `metacubex/protobuf-go`；依赖里的 replace 不会传递，本模块用的是上游
protobuf，编译和测试都正常。

## 4. 控制协议

两个平台共用同一组 Protobuf 消息和同一个 Go 分发器，区别只在传输层和"启停"这类宿主方法。
协议只有请求和响应两种消息，没有服务端主动推送，因为 macOS 的 provider message 只支持一问一答。
schema 放在 `proto/solaris/protocol/control.proto`，Go 与 Python 的绑定由它生成。
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
| `Start` | Windows | 提交配置（YAML 或拼装来源）、覆写和运行选项，然后准入、应用、持久化 |
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
| `InspectSource` | 按拼装的方式读一份订阅：节点及其地区、有没有规则、策略组和规则的数量 |
| `ListGeoCategories` | home 里 GEO 数据的 GEOSITE / GEOIP 分类名，以及文件的 sha256 和修改时间 |

`PreviewGroup`、`MatchRule`、`ConvertToPlan`、`ExportPlan`（routing.md 8.2 节，M8b）在 `Request` / `Response` 里预留了
字段号 51 到 54。

方法的行为约定：

- `Validate`、`Start`、`Reload` 携带配置来源、覆写列表 `overrides`（先全局、后当前配置，见 3.2 节）和
  `RunOptions`（`Validate` 没有），经过 3.2 节的准入流水线。配置来源是 oneof：订阅原文 `config_yaml`，或拼装
  来源 `composition`（节点池的订阅、信息节点表达式、方案 JSON，routing.md 8.1、8.5 节）。用 `composition` 时
  三个方法返回 `CompositionReport`，用 `config_yaml` 时返回 Empty。
  `mixed_port` 超过 65535 时返回 `invalid_request`。
- `ListRules` 的 `Rule.entry_id` 和连接快照的 `Connection.rule_entry_id` 是运行中的规则来自哪个规则条目
  （兜底是 `fallback`）；订阅自带的规则和覆写插入的规则为空。
- `InspectSource`、`ListGeoCategories` 只读，不需要内核在运行，两种宿主都提供。`InspectSource` 读不了订阅时
  返回 `invalid_config`；`ListGeoCategories` 在 home 里没有某个文件时那一类为空，`ServiceHost` 改用应用附带的
  分类名清单。
- `Stop` 对已停止的内核不做任何事。开发模式下 `Claim` 直接成功，因为开发 socket 只接受运行服务的那个用户。
- 内核未运行时，列表类查询（代理组、provider、规则）返回空列表，改变状态的内核方法返回 `not_running`；
  `GetStatus` 和订阅随时可用。
- 宿主方法只在 `HostMethods` 打开时提供（Windows 服务和开发服务），其余宿主返回 `unsupported_method`；
  `Reload` 和内核方法在两种宿主上都有。
- 系统代理：开发服务对开启请求（`SetSystemProxy`，以及 `RunOptions.system_proxy_enabled`）返回
  `unsupported_method`，关闭请求直接成功；Windows 服务在 M4 实现之前返回 `not_implemented`。

错误码：

| 码 | 含义 | retryable |
| --- | --- | --- |
| `invalid_request` | 请求本身不合法：缺少字段、未知枚举、不存在的组或节点、组不接受选择等 | 否 |
| `unsupported_protocol_version` | 协议版本不一致 | 否 |
| `unsupported_method` | 当前宿主不提供该方法或该能力（如开发服务上的 TUN 和系统代理） | 否 |
| `not_implemented` | 方法已定义、尚未实现 | 否 |
| `invalid_config` | 配置无法解析，或被准入流水线拒绝；`message` 是 mihomo 的原始错误文本。拼装出错时以 `plan: ` 或 `subscription "名称": ` 开头 | 否 |
| `not_running` | 需要运行中的内核 | 否 |
| `already_running` | 内核已在运行时调用 `Start`（切换配置走 `Reload`） | 否 |
| `apply_failed` | 配置已应用，但端口或 TUN 没有打开；原因在 mihomo 日志里 | 是 |
| `unknown_subscription` | 订阅不存在、已过期，或属于其他调用者 | 否 |
| `too_many_subscriptions` | 同时存在的订阅超过上限 | 是 |
| `operation_failed` | 内核操作失败，如测速超时、provider 更新失败 | 是 |
| `internal_error` | 无法编码响应 | 否 |

### 4.4 订阅

协议没有服务端推送，订阅由"服务端缓存 + 客户端拉取"实现：

- 订阅建立后，分发器为它维护一个有界缓冲区，持续写入事件。
- 客户端定期调用 `PollEvents` 取走缓冲区里的事件。
- 缓冲区满时丢弃最旧的事件，下一次 `PollEvents` 的结果里带上丢弃数量。单个慢客户端不会阻塞内核。
- 超过 30 秒没有被拉取的订阅自动失效。Windows 上连接断开时，该连接的订阅也立即失效。

| 订阅 | 事件 | 建议拉取间隔 | 缓冲上限 |
| --- | --- | --- | --- |
| 日志 | 每条日志，按级别过滤（未指定时为 info） | 0.5 秒 | 1024 条 |
| 流量 | 每秒一条：上下行速率与累计值 | 1 秒 | 600 条 |
| 连接 | 每秒一份快照 | 1 秒 | 4 份（每份快照都取代前一份） |

实现细节：

- 订阅属于创建它的调用者（`peer.Identity.UserID`），其他调用者拉取或退订时得到 `unknown_subscription`。
  订阅 ID 是随机的 53 位整数。
- 同时最多 64 个订阅。单次 `PollEvents` 最多返回 `max_events` 条（0 表示不限），事件累计超过 4 MiB 时
  剩余的留到下一次，保证响应不超过 8 MiB 的帧上限。
- 有订阅时才运行一个采样协程：每秒采一次流量和连接（多个订阅共享同一份样本），并清理过期订阅；
  有日志订阅时才订阅 mihomo 的日志。没有订阅时两者都不运行。
- "连接断开即失效"靠分发器收到的 `ctx`：Windows 管道上它随连接结束而取消；macOS 的 provider message
  没有连接，只靠 30 秒过期。

两个平台用同一套拉取逻辑，UI 不需要为推送和拉取分别写代码。

## 5. Windows：特权服务

### 5.1 安装布局

```text
C:\Program Files\Solaris\            管理员所有，用户只读
├── Solaris.exe                      Nuitka standalone UI
├── solaris-service.exe              Go 服务
└── …                                  Python / Qt 运行依赖

C:\ProgramData\Solaris\              仅 SYSTEM 与 Administrators 可访问
├── service.log
├── owner.json                         当前属主 SID、会话
└── users\<SID>\                       该用户的 mihomo home 目录（IsSafePath 根）
    ├── config.yaml                    UI 提交的配置
    ├── overrides.json                 UI 提交的覆写（按应用顺序的 YAML 原文，ui.md §4）
    ├── options.json                   运行选项与 was_running
    ├── providers\ …                   provider 与规则集缓存
    └── cache.db                       mihomo profile 缓存
```

UI 必须使用 Nuitka standalone 模式。onefile 模式会解压到 `%TEMP%` 后运行，客户端路径校验会失败，
而且 `%TEMP%` 对用户可写。

### 5.2 服务

- 服务名 `SolarisService`，以 LocalSystem 身份运行，自动启动。
- SCM 故障恢复：依次在 5 s、15 s、60 s 后重启。
- 接受的控制事件：Stop、Shutdown、SessionChange、PowerEvent。PowerEvent 用于唤醒后重置连接。
- 启动时读取 `owner.json` 和对应用户的 `options.json`；若 `was_running` 为真，按保存的配置恢复隧道。
- 超时逐层递减，参考 hypervpn：UI 请求超时 > 服务处理超时 > 内核启动或停止超时。
  这样内核卡死时，服务的操作锁和 SCM 的停止流程不会被一并卡住。

### 5.3 管道与客户端校验

管道名为 `\\.\pipe\ProtectedPrefix\Administrators\solaris`，SDDL 为
`D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)`。这是服务唯一的对外入口。

`ProtectedPrefix\Administrators` 前缀下只有管理员和 SYSTEM 能创建管道，因此客户端不必再核对服务端
PID 来防止管道名被抢注。

服务在每条连接建立时校验客户端，任一项不通过就断开：

1. 用 `GetNamedPipeClientProcessId` 取得客户端进程，并在整个连接期间持有其进程句柄；
2. 客户端映像的最终路径必须是安装目录里的 `Solaris.exe`，并锁住文件句柄、比较文件 ID，防止中途被替换；
3. 读取客户端 token 的用户 SID 和会话 ID，作为后续属主判断的身份；
4. 发布构建额外要求客户端的 Authenticode 签名者与服务相同。

后三项的做法参考 sing-box `experimental/boxdd` 的 `peer_windows.go`。

### 5.4 属主

- 同一时间只有一个属主（用户 SID 加会话）。
- 属主以外的用户只能调用 `GetInfo`，其余方法一律返回 `owned_by_other_user` 错误。
- 属主注销时停止隧道，保留 `was_running`，属主下次登录后自动恢复（决策 D5）。TUN 是全局的，不停止的话，其他用户的
  流量会走属主的订阅。
- 内核运行中能否由管理员接管，待定。

### 5.5 系统代理

系统代理是每个用户 HKCU 下的设置，服务以 SYSTEM 身份直接写只会改到 SYSTEM 自己。
服务的做法是：用 `WTSQueryUserToken` 取得属主会话的 token，模拟该用户写入 Internet Settings 并通知
WinINet；内核停止时还原。开机恢复时，如果属主尚未登录，就等收到 SessionLogon 事件再写入。
实现参考 boxdd 的 `platform_windows.go`。

## 6. macOS：NetworkExtension

### 6.1 包结构

```text
Solaris.app/Contents/
├── MacOS/Solaris                                   Nuitka standalone UI
├── Frameworks/SolarisManager.dylib                 Swift，C ABI，供 ctypes 调用
└── Library/SystemExtensions/dev.tschen.solaris.tunnel.systemextension
    └── NEPacketTunnelProvider + 本仓库 Go 模块编出的 xcframework（静态链接）
```

- 源码、构建与签名见 [apple/README.md](../apple/README.md)：XcodeGen 工程 `apple/project.yml`，构建脚本
  `scripts/build_apple.py`。只构建 arm64（D2），最低系统版本暂定 macOS 15.0。
- 需要 Developer ID 签名、公证，以及 `packet-tunnel-provider-systemextension` entitlement。
- 管理库用 `OSSystemExtensionRequest` 激活扩展（用户需在系统设置中批准；包内的扩展总是替换其他构建激活的
  扩展）、配置 `NETunnelProviderManager`、启停隧道、查询状态、收发 provider message。C 接口是
  `apple/Manager/SolarisManager.h`：调用阻塞到完成或超时，要在后台线程调用；本库交出的字符串和缓冲区由
  调用方用 `solaris_manager_free` 释放。
- hypervpn-desktop 的 `src/packet-engine/native/macos/SystemNetworkExtension/` 和它的 NE 管理库可以作为参考。

### 6.2 扩展内的运行流程

1. 用 `setTunnelNetworkSettings` 设置路由、地址和 DNS：地址用 mihomo 的 TUN 默认值（198.18.0.1/30、
   fdfe:dcba:9876::1/126，MTU 9000），IPv4 和 IPv6 的默认路由都进隧道；DNS 服务器是 /30 里的另一个地址
   198.18.0.2，查询进入隧道后由 mihomo 的 dns-hijack 应答。fd 模式下 engine 必须用同样的地址；
2. 在打开的描述符里找到 utun 的控制 socket，以 fd 模式启动 engine。fd 模式尚未实现，目前 `startTunnel`
   在这一步以错误结束，不建立隧道；
3. 初始化 Go 分发器，配置和 Windows 一样由界面经控制协议的 `Start` / `Reload` 传入（决策 D7），不经
   `startVPNTunnel(options:)`。不监听任何 socket，mihomo 的 HTTP 控制器也不启动。

扩展自身发出的流量本来就不进入隧道，因此不用设置 socket 保护回调。

### 6.3 控制通道

macOS 不监听任何 socket，控制消息全部走 Network Extension 自带的 IPC：

1. UI 通过 ctypes 调用管理库的发送函数 `solaris_manager_send`，传入编码好的 Request 和超时；
2. 管理库调用 `NETunnelProviderSession.sendProviderMessage` 发给扩展，超时则返回错误；
3. 扩展在 `handleAppMessage` 里把消息交给 Go 分发器（xcframework 导出的 `solaris_handle_message`）。
   分发在并发队列上执行，不阻塞 provider 的其他回调；改变状态的方法由分发器的操作锁串行；
4. 分发器返回编码好的 Response，经 completion handler 回到管理库，再交回 Python。

- **访问控制由系统负责**：只有本应用（同一 Team ID、带 NE entitlement 的签名进程）能加载自己的
  `NETunnelProviderManager` 并取得 session，扩展内不再做对端校验。
- **只有隧道已连接时可用**：扩展进程只在隧道连接期间运行。未连接时发送函数返回 `NOT_CONNECTED`，UI 只能
  通过管理库查看连接状态。
- **方法与 Windows 相同，但没有宿主方法**：启停走管理库；`GetInfo`、`Reload` 和内核方法走 provider message。
- **消息大小**：单条 provider message 的上限需要实测，它决定 `Reload` 能否直接携带大配置。
- 做法与 hypervpn 一致，见它的 `NetworkExtensionManager.swift`（`providerExchange`）和
  `PacketTunnelProvider.swift`（`handleAppMessage`）。

## 7. Python UI

- 分层参考 hypervpn：视图（QML）→ ViewModel → models → 宿主客户端。asyncio 事件循环运行在后台线程，结果
  通过排队信号回到 Qt 线程。
- 控制客户端只依赖 protobuf：Windows 上是命名管道的一条长连接，macOS 上通过 ctypes 调用管理库。
- 宿主抽象 `TunnelHost` 有 `ServiceHost`（Windows 服务；开发模式下走本地 socket）和 `MacExtensionHost`（未实现）
  两个实现；测试和预览用模拟宿主。

界面各部分的设计见 [ui.md](ui.md)。

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
  模式，分发器标记为开发宿主（`Development`）。这样 `ServiceHost` 和全部方法都能在 Mac 上联调。这只是开发模式，
  不进入任何发布包；macOS 正式版走 provider message，不使用 socket。

  ```bash
  go -C src/core run ./service -socket /tmp/solaris-dev.sock
  ```

  `scripts/dev.py` 一步完成联调：构建并启动开发服务，界面的配置和设置放在单独的目录（默认临时目录），
  mihomo 的 home 放在其中的 `dev-core`，不碰用户数据目录；"登录时打开"在源码运行时本来就不可用。

- **本机开发不得改动本机网络**：开发机就是日常使用的机器，开发模式必须满足以下各条，并有测试覆盖
  （`internal/engine/admission_test.go` 的 `TestNoTUN*`，`service/service_unix_test.go` 用真实的开发服务接线验证）：
  - 不创建 TUN；用户配置或覆写里的 `tun`、`auto-route`、`auto-redirect`、`tproxy-port`、`redir-port` 一律被清除；
  - 混合端口和其他入站只监听 `127.0.0.1`，强制关闭 `allow-lan`；
  - 不设置系统代理，不修改路由和系统 DNS；
  - 请求开启 TUN 或系统代理的方法返回 `unsupported_method`。

  验证真实流量时，用 `curl -x http://127.0.0.1:<混合端口>` 走代理，并在启动前后用只读命令
  （`ifconfig -l`、`netstat -rn`、`scutil --dns`、`scutil --proxy`）确认本机网络没有变化。
- **TUN 只在隔离环境里测试**：自管模式在 CI 的 Linux runner 上、独立的网络命名空间里测试；wintun 和
  strict-route 在 Windows 测试虚拟机里验证；macOS NE 在单独的测试 Mac 或虚拟机上验证，不在开发机上运行。
  - `internal/engine/tun_linux_test.go` 有三道开关：只在 `-tags tuntest` 时编译，只在
    `SOLARIS_TUN_TEST=1` 时运行，并且拒绝在宿主机的网络命名空间里运行。本机执行 `go test ./...`
    不会编译它。
  - CI 的 `tun` job 建两个命名空间，用 veth 相连：`md-tun` 里以 root 运行测试，`md-srv` 里起一个 HTTP 服务。
    服务监听在 `md-srv` 回环口上的 10.201.0.1，不在 `md-tun` 的链路网段里：auto-route 让主表里比默认路由
    更具体的路由优先（`suppress_prefixlength 0`），同网段的目的地不进 TUN。
    测试用自管模式启动 TUN，经 TUN 访问 `md-srv`，并用 mihomo 的流量统计确认流量确实经过内核；再验证
    TUN 配置不变时 Reload 不重建网卡，关闭 TUN 或 Stop 后链路、策略路由和各路由表都恢复原样。
    矩阵覆盖 `system`、`mips` 栈，以及带 `with_gvisor` 的 `gvisor`、`mixed` 栈。
- **端到端测试**（`tests/e2e`，标记 `e2e`）：界面连真实的开发服务。每个测试起一个开发服务子进程，用
  `ServiceHost`、临时数据目录和真实的 ViewModel 组装界面，离屏加载 QML，通过 ViewModel 的槽操作，再用
  `curl -x` 经混合端口访问本机的 HTTP 服务；配置里的节点是本机的 HTTP 代理，它们记下经过的请求，所以能看出
  流量走了哪条路。覆盖导入文件和订阅、连接、各页数据、切换模式和节点、TUN 与系统代理被拒、覆写 / 订阅更新 /
  切换配置时重载不断开已有连接、断开重连与服务重启、混合端口被占用和局域网连接，以及模拟宿主与真实服务对一组
  配置的判定一致；自定义分流（B2）另有用例：只有节点的订阅加方案连接、域名规则把流量导向指定节点、连接快照带规则
  条目 id、重载改方案。分流用例需要 GEO 数据，用 `scripts/fetch_geodata.py` 拉取并缓存。默认跳过，设置
  `SOLARIS_E2E=1` 才运行；`SOLARIS_E2E_SERVICE` 可以指向已
  构建的服务，否则测试自己 `go build`；`SOLARIS_E2E_SCREENSHOTS` 指向一个目录时保存各页截图。CI 的
  `e2e` job 在 macOS runner 上先构建服务再运行。全程只用回环地址，不需要 TUN。
- **测试覆盖**：
  - engine：配置流水线的回归测试，对应 3.3 节各项；开发模式护栏；启停与重载；50 次启停的泄漏检查；
  - 控制协议：帧头校验、超限拒绝、请求 ID 匹配、订阅缓冲与过期，Go 与 Python 两端用同一组样本互测
    （样本里的响应来自一个不链接 mihomo 的假 engine，所以不随 mihomo 版本变化）；
  - Windows CI：服务的客户端校验、管道 SDDL、属主与恢复流程；
  - Python：用模拟宿主测试 models 与 ViewModel；模拟宿主的配置检查、错误码和措辞跟着真实服务走，端到端测试
    核对两者一致。
- **竞态检查**：`go test -race` 会报出 mihomo 自身在 `log.SetLevel` 上的竞态（见 3.2 节），所以 CI 不带
  `-race`；分发器的订阅代码可以单独用 `-race` 检查。
- **需要真机验收**：真实 TUN 流量、系统代理模拟写入、安装与升级、系统扩展激活。

## 12. 待定事项

待决策的事项、最晚何时决定以及已经做出的决策，统一记录在 [roadmap.md](roadmap.md) 的决策一节。

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
