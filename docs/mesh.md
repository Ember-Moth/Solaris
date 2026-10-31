# 组网：Tailscale、ZeroTier、EasyTier

mihomo 自带三种组网出站：`tailscale`、`zerotier`、`easytier`。它们用来访问自己的设备和内网，不是代理出口。
本文写怎样把它们和代理节点分开，以及在应用里添加网络（第 10 节）。决策是 [decisions.md](decisions.md) 的 D24；
界面的细节见 [ui.md](ui.md) 第 9 节。

## 1. 目标与边界

**保留**（D24）：三种出站都编进内核，体积不变（[architecture.md](architecture.md) §3.4）。

**单独处理**：

- 不进节点池，不进"自动选择"、地区组和自建组，也不测速；
- 在分流页单独列出，作为规则的目标，例如"`100.64.0.0/10` → 家里的 Tailscale"；
- 只用作出站。凡是让组网里的其他设备经本机出去、或访问本机所在局域网的选项，一律关闭（第 4 节）。
  Windows 上内核以 SYSTEM 身份运行，macOS 上运行在系统扩展里，这些选项会把用户的网络暴露给组网里的其他设备。

**范围**：与链式出口一样（D21），单独列出和作为规则目标只在"我的方案"下提供。"订阅自带"的配置照原样使用，
组网出站在里面怎么写就怎么用；但第 4 节的安全项对所有配置都生效。

## 2. 组网出站从哪里来

两个来源：

- **订阅或本地配置文件**（D24）。代理服务商的订阅里不会有组网出站，因为它们都需要用户自己的账户、网络 ID 或密钥。
  用户自己写一份本地配置（例如只含一个带 `auth-key` 的 `tailscale` 出站），导入后加入节点池，就会出现在组网一节。
- **在应用里添加**（第 10 节）：凭据存系统凭据库，不进配置文件，也不进备份。

## 3. 拼装

在内核拼装节点池时（[routing.md](routing.md) 8.1 节）：

- 读订阅时，类型为 `tailscale`、`zerotier`、`easytier` 的出站不进节点池。信息节点表达式不作用于它们。
- 它们写进顶层 `proxies`，不进任何内联 proxy-provider。拼装生成的策略组都用 `use` 加 `filter` 引用 provider，
  所以不会把它们算作成员；只有覆写加进来的 `include-all` 组会包含它们。
- 重名的处理与节点相同：与其他节点、组网出站、应用内的网络、策略组、链式出口（含 VPN Gate 的服务器）或 mihomo
  内置名称重名时，加" · 订阅名"。节点池和组网出站按订阅顺序一起取名。
- 方案里用 `mesh:<订阅 id>/<原名>` 引用，原名是订阅里写的名称，改名不影响引用。规则目标和兜底目标都可以写它；
  策略组的 `others` 不能写（`invalid_config`）。`mesh:` 后面没有"订阅 id/名称"也是 `invalid_config`。
- 引用的出站已经不存在时（订阅删了、节点改了名），规则改写为 `REJECT`，并在报告里给出警告 `mesh_missing`
  （`target` 是方案里写的引用）。不改走"节点选择"：发往私有地址的流量走普通代理也到不了，还会让这些地址经代理节点出去。
- 自建组的成员条件（地区、来源订阅、名称、全部节点）不匹配组网出站；手动挑选也不列出它们。
- `CompositionReport` 有 `mesh` 列表：`id`、名称、类型（`Tailscale`、`ZeroTier`、`EasyTier`）、来源订阅，按订阅
  顺序；节点池（`nodes`）里没有它们。`InspectSource` 的节点多一个标记 `mesh: true`，`info` 总是 false。订阅页上
  的节点数都不含组网出站和信息节点（`SourceInspection.node_count`）：订阅行的节点数、删除订阅时提示的"节点池会少
  N 个节点"，以及还没有拼装报告时按各订阅相加的合计；有报告时合计取报告的 `nodes`，同样不含。
- 分享导出（routing.md 8.8 节）写组网出站的名称；`ConvertToPlan` 带来源订阅 id 时，把指向这份订阅里组网出站的
  规则转成 `mesh:` 目标，导入（没有来源）时跳过这些规则（`target`）。策略组里列出的组网出站不进成员。
- 实现在 `internal/compose`（`pool.go` 分出组网出站，`compose.go` 的 `target`），测试是 `egress_test.go`。

## 4. 安全项

准入流水线的最后一步（[architecture.md](architecture.md) §3.2、§3.3），对所有模式、所有配置来源（订阅原文、拼装、
覆写之后）生效：

| 出站 | 强制 | 原因 |
| --- | --- | --- |
| `easytier` | `enable-exit-node: false`，`proxy-networks` 清空，`mapped-listeners` 清空 | 前两项会让组网里的其他设备把本机当作出口、访问本机所在的子网；后一项向外公布监听地址 |
| `easytier` | `listeners` 删掉，`no-listener: true` | 写了 `listeners` 或 `no-listener: false` 时，它在这些地址上（后者是所有网卡的 `tcp://0.0.0.0:11010`）接受对等点的连接。这样一来没有 `peers` 的 EasyTier 出站无效：mihomo 要求不监听时写 `peers` |
| `tailscale` | 无需改动 | 只用作出站：`exit-node` 是使用别人的出口，不是提供出口 |
| `zerotier` | 无需改动 | 只加入网络；`primary-port` 等是打洞用的 UDP 端口 |

- 三者的 `state-dir` 已由 mihomo 用 `IsSafePath` 限制在 home 里（v1.19.33 源码核实），越不出去。§3.3 的回归测试
  `TestSecurity6MeshOutboundsOnlyGoOut` 覆盖这三种出站：`state-dir` 指向 home 之外（`../` 和绝对路径）时配置被
  拒绝。Tailscale 出站只在 `with_gvisor` 构建里有，不带这个标签时 mihomo 拒绝任何 Tailscale 出站，所以 CI 的核心
  测试另外带 `with_gvisor` 跑一次这组测试。

强制的做法（`internal/admission/mesh.go`，安全项的最后一步）：

- 顶层 `proxies` 和每个 proxy-provider 的 `payload` 里，类型为 `easytier` 的出站：删掉 `proxy-networks`、
  `mapped-listeners`、`listeners`，`enable-exit-node` 写成 false，`no-listener` 写成 true。
- 远程（`http`、`file`）provider 的内容在应用之后才下载，流水线看不到；provider 自己的 `override-expr` 还可以把
  任何出站改成 `easytier` 并打开这些选项。所以这类 provider，以及带 `override-expr` 的内联 provider，在
  `override.override-expr` 末尾追加五条表达式，例如
  `(select(.type == "easytier") | .enable-exit-node) = false`。mihomo 对 provider 读到的每个出站按顺序执行
  这些表达式，追加的最后执行；`exclude-type` 在表达式之前判断，挡不住改类型，所以不用它。测试用 mihomo 自己的
  表达式求值器核对结果。
- 强制项改写后在报告里给出警告 `mesh_restricted`：`entry_id` 是出站的 `mesh:` id（覆写加进来的出站没有 id，
  用它的名称），`target` 是被关闭的选项名，每项一条（`no-listener` 原来写了 false 时也算一条）。只有原来打开的
  选项才有警告；远程 provider 里的出站改写时内核看不到，没有警告。用 `config_yaml` 时没有报告，也就没有警告，
  但改写照样生效。

## 5. 界面

- **分流页·策略组**：内置、地区、自建之后是"组网"一节，排在"链式出口"之前，"我的方案"下总是显示。先列应用内
  的网络（10.7 节），再列订阅带来的组网出站，最后一行是"添加网络…"。订阅带来的组网出站每行写名称、类型和来源
  （"Tailscale · 来自「家里」"）；名称是报告里生成配置用的名称（重名时带" · 订阅名"），`mesh_restricted` 警告写在
  说明末尾："· 已关闭 选项"。"更多"菜单：
  - "添加常用规则"：对 Tailscale，直接加一条 IP 段规则（`100.64.0.0/10`、`fd7a:115c:a1e0::/48`，`no-resolve`）和
    一条域名规则（`ts.net`），目标都是这个出站，插在最前面，方案里已有一样的条目就跳过，标题栏给出撤销提示；对
    ZeroTier 和 EasyTier，打开"添加规则"的"IP 段"，目标预选好，地址段由用户填，因为每个网络不同；
  - "使用它的规则"：把规则列表的目标筛选设为这个出站，并切到规则标签。
- **添加规则对话框**：目标下拉框里，组网出站排在自建策略组之后、链式出口之前（routing.md 4.4 节）。
- **代理页**：组网出站不是策略组，不在策略组列表里。
- **规则行**：目标已经不存在时，目标取 `mesh:` 引用里的原名，后面写 `warning` 色的"不存在，已拒绝连接"。
- **备份**：快照包含订阅链接和本地配置原文（第 7 节）；数据范围见 [隐私说明](../PRIVACY.md)，界面按 D30 不额外弹出确认框。
- 文案："组网" Mesh networks。

## 6. 协议

字段在 `control.proto`（`MeshOutbound`、`CompositionReport.mesh`、`SourceNode.mesh`）和 `host/base.py` 里。

- 方案 JSON（routing.md 8.5 节）的 `target` 和 `fallback` 可以写 `mesh:<订阅 id>/<原名>`，`version` 仍是 1。
- `CompositionReport` 有 `mesh` 列表和警告原因 `mesh_missing`、`mesh_restricted`；`InspectSource` 的节点有 `mesh`
  标记。
- 订阅带来的组网出站不需要新方法；应用内组网的 `Composition.mesh`、`GetMeshStatus`、`LoginMesh` 见 10.5 节。

## 7. 存储与备份

- 方案里的 `mesh:` 引用随方案进入备份。
- 订阅和本地配置文件里的组网出站，凭据（Tailscale 的 `auth-key`、ZeroTier 的 `identity-secret`、EasyTier 的
  `network-secret`）写在配置原文里。订阅下载的内容不进备份，但**本地文件配置的 YAML 原文进备份**（storage.md
  第 3 节），所以这些凭据会进入未由应用额外加密的快照，并随所选方式备份或导出。数据范围和保护方式见
  [隐私说明](../PRIVACY.md)；界面按 D30 不额外弹出确认框。
- 在应用里添加的网络（第 10 节）凭据存系统凭据库，不进备份；网络本身（类型、名称、网络 ID 等）进备份，见 10.2 节。

## 8. 待实测

下面几项要有真实的网络和账户才能确认，做 [maintenance.md](maintenance.md) 2.7 节的验收时一并记下。

- EasyTier 不监听时能否打洞。第 4 节对所有 EasyTier 出站删掉 `listeners`、写 `no-listener: true`；v1.19.33 这时
  要求写 `peers`（"implicit public.easytier.top is disabled"）。实测：两台不在同一局域网的机器，一台是开发服务，
  另一台跑官方 easytier-core，同一个 `network-name` / `network-secret`，开发服务这边写对端的 `peers`；看对端
  `easytier-cli peer` 里连接类型是 p2p 还是经中继，以及经组网出站访问对端的延迟。不能打洞时，再比较 `listeners`
  只写 `127.0.0.1` 的地址（加 `peers`）能否打洞，据此决定第 4 节是继续强制不监听，还是只限制监听地址。
- Tailscale 的 MagicDNS 名称在 fake-ip 下能否解析：出站拿到的是域名，要由 tsnet 解析。
- 在国内直连时，Tailscale 的控制面和 DERP 中继能否连上。连不上时给网络设经由（10.2 节）；经由代理后点对点打洞
  会失效，只能走中继。

## 9. 验收

**组网出站的单独处理**（D24）的自动测试都不联网：

- 内核：组网出站不进节点池和策略组、`mesh:` 目标、`mesh_missing`、`InspectSource` 的标记、分享导出与转换
  （`compose/egress_test.go`）；第 4 节的安全项（`TestSecurity6MeshOutboundsOnlyGoOut`，CI 另带 `with_gvisor`
  跑一次）；订阅的组网出站有流量才启动、EasyTier 改成不监听并给出 `mesh_restricted`
  （`TestSubscriptionMeshOutboundsWaitForTraffic`）。
- 界面：组网一节的行、"添加常用规则"、引用的出站不存在时拒绝连接（`tests/test_routing_exits.py`，模拟宿主）。

经真实网络的验收见 [maintenance.md](maintenance.md) 2.7 节；应用内组网的测试见 10.8 节。

## 10. 应用内组网

用户在应用里添加 Tailscale、ZeroTier、EasyTier 网络，不用自己写配置文件。网络是内核拼装出的组网出站，与第 3 节
订阅带来的组网出站一样不进节点池、作为规则目标，第 4 节的安全项照样生效。与第 1 节相同，只在"我的方案"下运行：
"订阅自带"的配置里没有它们。

### 10.1 mihomo 能提供什么

按钉住的 v1.19.33 核实（`adapter/outbound/{tailscale,zerotier,easytier}.go`）：

| | Tailscale | ZeroTier | EasyTier |
| --- | --- | --- | --- |
| 实现 | tsnet，`Tun` 不设，所以是假 TUN、假路由、空 DNS 管理器（`wgengine.NewUserspaceEngine`），只有 gVisor 网络栈 | zerotier-go，网络栈是 mipstack 或 gVisor（`newIPStack`） | easytier-core 的 WASM，在 wazero 里运行；`ApplyRequiredFlags` 强制 `no_tun = true`、`bind_device = false` |
| 对本机网络的改动 | 无：不建 TUN、不改路由和 DNS，只开 UDP/TCP 套接字 | 无，同左 | 无，同左 |
| 何时启动 | 第一个连接或第一次 DNS 查询时（`ensureStarted`） | 同左 | 同左 |
| 登录 | 没有 `auth-key` 时 tsnet 进入 NeedsLogin，自己发起交互式登录，`LocalClient().Status()` 的 `AuthURL` 是登录链接；`StartLoginInteractive` 再要一个 | 网络开了 SSO 时 `authURL` | 无 |
| 状态 | `LocalClient().Status()`：`BackendState`、`Self.TailscaleIPs`、`Peer`（各自 `Online`）、`CurrentTailnet` | `runtime.node`（`Address`、`Online`、`Peers`）、`config.Assigned`、`networkErr`、`authURL` | `instance.State()`、`ShowNodeInfo`（本机地址）、`ListRoute`（其他设备） |
| 经由 | `dialer-proxy` 作用于 tsnet 的拨号和 UDP（控制面、DERP、打洞） | 作用于传输层，设了就不共用 UDP 端口 | 作用于全部套接字（`component/easytier/platform.go`） |

- 三者都**没有公开的状态接口**：tsnet 服务器、zerotier 运行时、easytier 实例都在未导出的字段里，REST API 也只给
  名称和类型。内核用 `reflect` 读这些字段（`internal/engine/meshstate*.go`），并照各自的锁规则加锁（ZeroTier 的
  `stateMu`、EasyTier 的 `mu`）；Tailscale 只在 `backendInitCh` 关闭之后才碰 tsnet，那时它已经启动完毕，调用
  `LocalClient()` 不会另起一个。字段名和类型由测试对构造出来的出站逐个核对，升级 mihomo 时测试不过就要改这里。
- mihomo 重新加载时**不关闭被替换的出站**（`tunnel.UpdateProxies` 只换表），靠 GC 的 finalizer 关闭。旧的 tsnet
  或 ZeroTier 节点可能和新的同时用同一个状态目录，所以内核自己管理组网出站的生命周期（10.4 节）。
- 三者的构造函数都不联网；Tailscale 和 EasyTier 在构造时按名称注册一个 DNS 客户端（`tailscale://名称`），
  只给配置里这样写的 nameserver 用，拼装出的配置不用。

### 10.2 数据与存储

**表** `mesh_networks`（本地数据库，`database.py` 里单独一块；`user_version` 仍是 1）：

| 列 | 内容 |
| --- | --- |
| `id` | UUID（32 位十六进制），方案用 `mesh:<id>` 引用 |
| `kind` | `tailscale` / `zerotier` / `easytier` |
| `name` | 用户起的名称，也是出站名称；网络之间不重名 |
| `via` | 经由：空是直连（默认），否则是策略组 id（`proxy`、`auto`、`region:XX`、自建组 id） |
| `settings` | 类型自己的设置，JSON，不含凭据（下表） |
| `position`、`created_at`、`updated_at`、`deleted_at` | 与其他表相同 |

| 类型 | `settings` | 凭据（系统凭据库） |
| --- | --- | --- |
| Tailscale | `control_url`（空是 Tailscale 自己的，否则如 Headscale）、`hostname`（在 tailnet 里的设备名，默认取本机名） | `auth_key`，可选；没有就在浏览器里登录 |
| ZeroTier | `network`（16 位十六进制网络 ID） | `identity_secret`，可选；没有就由内核生成，存在状态目录 |
| EasyTier | `network_name`、`peers`（对等节点 URI 列表，至少一个，如 `tcp://example.com:11010`）、`ipv4`（如 `10.144.144.2/24`，空则由网络分配）、`hostname` | `network_secret`，必填 |

- **凭据**存系统凭据库（D14，`models/credentials.py` 的 `MeshSecrets`），键 `mesh:<id>`，值是紧凑 JSON，只写
  非空项。删除网络时一并删除。读不出来（钥匙串锁着）时按"没有凭据"处理：Tailscale、ZeroTier 仍可运行（登录状态
  和身份在状态目录里），EasyTier 缺网络密钥不交给内核，行上写"缺少网络密钥"，指向它的规则拒绝连接。
- **经由**跟着网络存，不进方案：网络属于设备，而经由引用的是"当前方案"的策略组。拼装时按当前方案解析，找不到或
  没有成员就经"节点选择"，报告给出 `via_empty`（10.3 节）。
- **备份**：网络进快照（`mesh` 列表，字段同表，`settings` 是 JSON 值；删除的是墓碑），凭据不进。这样方案里
  `mesh:<id>` 的规则在新设备上恢复后仍然指向同一个网络：Tailscale 在浏览器里重新登录（或重新填授权密钥），
  ZeroTier 得到新的身份、需要在网络里重新授权（或重新填身份密钥），EasyTier 重新填网络密钥。恢复按 `id` 替换
  或合并，与方案相同（storage.md 第 3 节）。
- **内核的状态目录**：每个网络在内核 home 下有 `mesh/<id>/`（Tailscale 的 `tailscaled.state`、ZeroTier 的身份和
  网络配置、EasyTier 的实例 id）。Windows 是属主的 home（architecture.md §3.2），macOS 是系统扩展的容器。它不属于
  界面的数据，不进备份。

### 10.3 拼装

`Composition.mesh` 是全部网络（10.5 节）。内核为每个网络生成一个顶层 `proxies` 出站，与第 3 节的组网出站一样
不进任何 provider 和策略组，列在报告的 `mesh` 里（`source_id` 为空）。

- **名称**：网络的名称。策略组、链式出口（含 VPN Gate 的服务器）和 mihomo 内置名称先取名；与它们或前面的网络
  重名时加" · 类型"（"家里 · Tailscale"），再重名加数字。之后才轮到节点和订阅的组网出站，与网络重名的按第 3 节
  加" · 订阅名"。
- **id 和名称的检查**：id 只能是字母、数字、`-`、`_`，最长 64；名称同策略组（不空、首尾无空格、没有逗号和换行）。
  不合格、id 重复、缺必填项（ZeroTier 网络 ID 不是 16 位十六进制、EasyTier 没有网络名称、网络密钥或对等节点）
  都是 `invalid_config`，message 以 `mesh network "<名称>": ` 开头。
- **规则目标** `mesh:<id>`（没有 `/`）；`mesh:<订阅 id>/<原名>` 不变。网络不在 `Composition.mesh` 里时同第 3 节：
  规则改写为 `REJECT`，警告 `mesh_missing`。
- **经由**：`via` 非空时写 `dialer-proxy: <策略组名称>`；策略组不存在或没有成员时经"节点选择"，警告 `via_empty`
  （`entry_id` 是 `mesh:<id>`，`target` 是 `via`）。`DIRECT`、`REJECT`、`region:*` 是 `invalid_config`。
- **生成的出站**（字段按 v1.19.33 的 `*Option` 核实）：

```yaml
- name: 家里
  type: tailscale
  state-dir: mesh/<id>
  hostname: <hostname>          # 有才写
  auth-key: <auth_key>          # 有才写
  control-url: <control_url>    # 有才写
  accept-routes: true           # 能访问对方发布的子网；只影响这个出站，不改系统路由
  udp: true
  dialer-proxy: <经由组名称>     # 有经由才写
- name: 公司
  type: zerotier
  network: <16 位网络 ID>
  state-dir: mesh/<id>
  identity-secret: <identity_secret>   # 有才写
  udp: true
- name: 实验室
  type: easytier
  network-name: <network_name>
  network-secret: <network_secret>
  peers: [tcp://example.com:11010]
  ipv4: 10.144.144.2/24         # 没有时写 dhcp: true
  hostname: <hostname>          # 有才写
  state-dir: mesh/<id>
  no-listener: true             # 不接受对等点的连接（10.6 节）
  enable-exit-node: false
  udp: true
```

- 分享导出（routing.md 8.8 节）写网络的名称，与订阅的组网出站相同。

### 10.4 运行：启动、重新加载与状态目录

内核在 Start、Reload 时（`internal/engine/mesh_run.go`）：

- **立即启动应用内的网络**（`Composition.mesh` 的，报告里没有来源订阅的那些），不等第一个连接：应用配置之后，
  每个在自己的 goroutine 里调用一次出站的 `DialContext`，带一个已取消的 context。三者的 `ensureStarted` 都先启动、
  再等待，所以这一调用只启动，不建立连接；返回的错误不是 context 取消时记下来，作为状态的 `error`。这样状态里
  马上有登录链接、ZeroTier 节点 ID 等。
- **订阅和"订阅自带"配置里的组网出站**，以及 provider 里的，仍按 mihomo 的默认，第一个连接时才启动：用户没有
  要求它们加入网络，订阅也不可信。它们启动前的状态是 `STARTING`。
- **配置没变的出站沿用**：重新加载时，名称相同、生成的映射（按键排序的 JSON）也相同的组网出站，把正在运行的
  出站放进新解析出来的 `adapter.Proxy`（它的 `ProxyAdapter` 字段是导出的），新解析出的那个还没启动就关闭。
  GLOBAL 和覆写加进来的 `include-all` 组拿的都是这个 `Proxy`，所以都经正在运行的出站；正在经它的连接（例如 SSH）
  不断。改了的、删了的旧出站在 `hub.ApplyConfig` **之前**关闭，不等 GC：配置一应用，就可能有连接启动新的那个，
  旧的不能还占着同一个状态目录。
- 构造时注册的 DNS 客户端（10.1 节）在沿用时随新解析出的那个一起注销，`tailscale://名称` 的 nameserver 要等下次
  设置改变才回来；拼装出的配置不用它。
- **停止**时关闭全部组网出站。
- **状态目录的清理**：用 `Composition` 的 Start、Reload 之后，`<home>/mesh/` 下不属于 `Composition.mesh` 任何一个
  网络的目录删掉，所以删除网络就删掉了它的登录状态和身份。Validate、PreviewGroup 等不清理；"订阅自带"的配置
  不清理。

### 10.5 协议

`control.proto`：

- `Composition.mesh = 5`：`repeated MeshNetwork`。`MeshNetwork` 有 `id`、`name`、`via`，和 `oneof network`：
  `TailscaleNetwork`（`auth_key`、`control_url`、`hostname`）、`ZeroTierNetwork`（`network_id`、
  `identity_secret`）、`EasyTierNetwork`（`network_name`、`network_secret`、`peers`、`ipv4`、`hostname`）。
  凭据与 WARP 账户一样：界面每次 Validate、Start、Reload 交给内核，内核只放内存；例外是 Windows 服务为开机恢复
  保存的运行副本（architecture.md §5.1），在只有 SYSTEM 和属主能读的目录里。
- **`GetMeshStatus`（57）**：运行中配置顶层的每个组网出站一条 `MeshStatus`，按配置顺序：

| 字段 | 内容 |
| --- | --- |
| `name`、`id`、`type` | 出站名称；拼装出来的有 `mesh:` id（订阅的或网络的），否则为空；`Tailscale` / `ZeroTier` / `EasyTier` |
| `state` | `STARTING` 启动或连接中；`NEEDS_LOGIN` 要在浏览器里登录；`WAITING` 等网络管理员授权（Tailscale 的 NeedsMachineAuth、ZeroTier 的 access denied）；`ONLINE`；`FAILED` 见 `error` |
| `login_url` | `NEEDS_LOGIN` 时的登录链接，可能暂时为空 |
| `addresses` | 本机在网络里的地址，CIDR（`100.101.102.103/32`、`10.147.17.5/24`） |
| `peer_count` | 其他设备：Tailscale 在线的对等点，ZeroTier 有路径的叶子节点，EasyTier 的路由条目 |
| `node_id` | ZeroTier 的 10 位节点 ID（管理页按它授权）；其他为空 |
| `network_name` | tailnet 名称、ZeroTier 网络名称或 EasyTier 网络名称 |
| `error` | `FAILED` 时 mihomo 的原文 |

  只读，不持有 engine 的操作锁；内核没在运行时 `not_running`。每个出站读状态最多等 2 秒。
- **`LoginMesh`（58）**：`name` 是出站名称，返回 `login_url`。Tailscale 调 `StartLoginInteractive`，等到有
  `AuthURL`（最多 20 秒）；ZeroTier 返回当前的 SSO 链接，没有时 `invalid_request`；EasyTier `invalid_request`。
  已登录的 Tailscale 也可以调用，用来换账户。超时 `operation_failed`（`retryable`）。
- 字段号 56 是 VPN Gate 的，这里从 57 开始。

### 10.6 安全项

- 网络只用作出站：Tailscale 不写 `exit-node`（那是用别人的出口，不提供）；EasyTier 写 `no-listener: true`、
  `enable-exit-node: false`，不写 `proxy-networks`、`mapped-listeners`、`listeners`；第 4 节对所有 EasyTier 出站
  强制同样的几项，覆写和订阅都改不了。ZeroTier 没有提供服务的选项。
- 订阅带来的组网出站不提前启动（10.4 节）：不可信的订阅不能让内核一启动就加入它指定的网络。
- 状态目录是相对 home 的 `mesh/<id>`，id 只有字母、数字、`-`、`_`，越不出 home；mihomo 的 `IsSafePath` 是兜底。
  清理只删 `<home>/mesh/` 下的直接子目录。
- 登录链接由内核从 tsnet 或 zerotier-go 取得，**由界面在用户的浏览器里打开**，内核不打开任何东西。界面只打开
  `https://` 和 `http://` 链接（自建的 Headscale 可能是 http），其他协议不打开。
- 凭据不写日志：生成的出站只在内存里；导出"生成的配置"（routing.md 8.8 节）时 `auth-key`、`identity-secret`、
  `network-secret` 和其他节点的密钥一样只剩名称、类型、地址和端口。

### 10.7 界面

- **组网一节**（分流页"策略组"标签，"我的方案"下总是显示）：先列应用内的网络，再列订阅带来的组网出站（第 5 节）。
  最后一行写"Tailscale、ZeroTier、EasyTier"（还没有组网时写"还没有组网：访问自己的设备和内网"），右边一个紧凑
  按钮"添加网络…"。网络的行：
  - 名称，说明"类型 · 细节"：在线时是本机地址和"N 台设备"；ZeroTier 等授权时是"本机 ID a1b2c3d4e5"；有经由时
    加"· 经由 节点选择"；`via_empty` 时"经由 X，暂经节点选择"。
  - 右边是状态文字：在线（正常色）、连接中…、需要登录、等待授权、缺少网络密钥（`warning`）、出错（`warning`，
    原因在说明里）、隧道未运行时"未连接"（静音色）。
  - 点行打开编辑对话框；"更多"菜单：登录…（Tailscale；已登录时用来换账户）、复制本机地址、添加常用规则、
    使用它的规则、编辑…、删除…（确认框写明"设备要到网络的管理页移除"）。
- **添加 / 编辑对话框**（`MeshDialog.qml`，宽对话框里两列，最小窗口也放得下）：类型（分段控件，只在添加时可选）、
  名称，然后是类型的字段：
  - Tailscale：登录方式（分段：浏览器 / 授权密钥）、授权密钥（选了才显示）、设备名、控制服务器（可选）；
  - ZeroTier：网络 ID、身份密钥（可选，密文输入）；
  - EasyTier：网络名称、网络密钥（密文输入）、对等节点（逗号分开）、本机地址（可选）；设备名默认取本机名，不显示；
  - 经由：下拉框，"直连"在前，然后是策略组（同 WARP 出口的经由，routing_text 的 `group_options`）。
  
  保存时先交给内核拼装校验（Validate），通过后写数据库和凭据库，再重新加载运行中的隧道。编辑时密钥框留空表示
  不改；凭据库里已有时占位写"已保存"。
- **登录**：Tailscale 用浏览器登录时，保存后状态一有登录链接就打开浏览器，只打开一次；之后在行的"更多"里随时
  可以再登录（已登录时用来换账户，`LoginMesh`）。隧道没运行时行上写"未连接"，"登录…"提示"连接后才能登录"。
- **状态**：分流页显示"策略组"标签、隧道在运行、有组网出站时，每 3 秒调用一次 `GetMeshStatus`。
- **添加常用规则**：Tailscale 同第 5 节；ZeroTier、EasyTier 打开"添加规则 · IP 段"，在线时预填本机地址所在的网段
  （`10.147.17.5/24` → `10.147.17.0/24`）。
- **目标下拉框**、**规则行的提示**与第 5 节相同；网络删掉后规则行的目标写 `mesh:<id>`，提示"不存在，已拒绝连接"。

### 10.8 测试与验收

**测试**（都不联网）：

- 内核：拼装的网络出站、名称冲突、`mesh:<id>` 目标、经由与 `via_empty`、检查失败（`compose/networks_test.go`）；
  mihomo 实际解析生成的三种出站（`engine/mesh_test.go`，Tailscale 用 `with_gvisor`）；读状态的反射对构造出来、
  没启动的出站逐个核对字段，状态映射用假数据测（`mesh_test.go`、`meshstate_tailscale_test.go`）；重新加载时沿用和
  关闭（EasyTier、ZeroTier 出站，启动函数换成假的）；状态目录清理；`GetMeshStatus`、`LoginMesh` 的派发
  （`control/mesh_test.go`）。
- 界面：表、凭据（假的 keyring 后端）、备份（`tests/test_mesh.py`）；拼装进 `Composition`
  （`tests/test_composition.py`）；视图模型和对话框（`tests/test_routing_mesh.py`，模拟宿主给出各种状态）。

**真机验收**：用自己的 Tailscale、ZeroTier 或 EasyTier 网络，在开发服务上做（不建 TUN），步骤见
[maintenance.md](maintenance.md) 2.7 节。
