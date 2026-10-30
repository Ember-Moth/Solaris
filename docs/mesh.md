# 组网：Tailscale、ZeroTier、EasyTier

mihomo 自带三种组网出站：`tailscale`、`zerotier`、`easytier`。它们用来访问自己的设备和内网，不是翻墙出口。
本文设计怎样把它们和翻墙节点分开。决策是 [roadmap.md](roadmap.md) 的 D24，实施计划是第二轮（随 M9）和 M11。

状态：设计稿（2026-10-30）。第二轮的内核部分已实现（2026-10-30），第 3、4、6 节按实现写成；界面部分未实现。

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

**第一版**（第二轮）：来自订阅或本地配置文件。

- 机场订阅里不会有组网出站，因为它们都需要用户自己的账户、网络 ID 或密钥。
- 用户自己写一份本地配置（例如只含一个带 `auth-key` 的 `tailscale` 出站），导入后加入节点池，就会出现在组网一节。

**M11**（首发后）：在应用里直接添加，凭据存系统凭据库（第 7 节）。

## 3. 拼装

在内核拼装节点池时（[routing.md](routing.md) 8.1 节）：

- 读订阅时，类型为 `tailscale`、`zerotier`、`easytier` 的出站不进节点池。信息节点表达式不作用于它们。
- 它们写进顶层 `proxies`，不进任何内联 proxy-provider。拼装生成的策略组都用 `use` 加 `filter` 引用 provider，
  所以不会把它们算作成员；只有覆写加进来的 `include-all` 组会包含它们。
- 重名的处理与节点相同：与其他节点、组网出站、策略组、"WARP 出口"或 mihomo 内置名称重名时，加" · 订阅名"。
  节点池和组网出站按订阅顺序一起取名。
- 方案里用 `mesh:<订阅 id>/<原名>` 引用，原名是订阅里写的名称，改名不影响引用。规则目标和兜底目标都可以写它；
  策略组的 `others` 不能写（`invalid_config`）。`mesh:` 后面没有"订阅 id/名称"也是 `invalid_config`。
- 引用的出站已经不存在时（订阅删了、节点改了名），规则改写为 `REJECT`，并在报告里给出警告 `mesh_missing`
  （`target` 是方案里写的引用）。不改走"节点选择"：发往私有地址的流量走普通代理也到不了，还会让这些地址经机场出去。
- 自建组的成员条件（地区、来源订阅、名称、全部节点）不匹配组网出站；手动挑选也不列出它们。
- `CompositionReport` 新增 `mesh` 列表：`id`、名称、类型（`Tailscale`、`ZeroTier`、`EasyTier`）、来源订阅，按订阅
  顺序。`InspectSource` 的节点多一个标记 `mesh: true`，这些节点不计入节点数，`info` 总是 false。
- 分享导出（routing.md 8.8 节）写组网出站的名称；`ConvertToPlan` 带来源订阅 id 时，把指向这份订阅里组网出站的
  规则转成 `mesh:` 目标，导入（没有来源）时跳过这些规则（`target`）。策略组里列出的组网出站不进成员。
- 实现在 `internal/compose`（`pool.go` 分出组网出站，`compose.go` 的 `target`），测试是 `egress_test.go`。

## 4. 安全项

准入流水线的最后一步（[architecture.md](architecture.md) §3.2、§3.3），对所有模式、所有配置来源（订阅原文、拼装、
覆写之后）生效：

| 出站 | 强制 | 原因 |
| --- | --- | --- |
| `easytier` | `enable-exit-node: false`，`proxy-networks` 清空，`mapped-listeners` 清空 | 前两项会让组网里的其他设备把本机当作出口、访问本机所在的子网；后一项向外公布监听地址 |
| `easytier` | `listeners`：待定（第 8 节） | 它在所有网卡上接受对等点的连接 |
| `tailscale` | 无需改动 | 第一版只用作出站：`exit-node` 是使用别人的出口，不是提供出口 |
| `zerotier` | 无需改动 | 只加入网络；`primary-port` 等是打洞用的 UDP 端口 |

- 三者的 `state-dir` 已由 mihomo 用 `IsSafePath` 限制在 home 里（v1.19.33 源码核实），越不出去。§3.3 的回归测试
  （`TestSecurity6`）加上了这三种出站：`state-dir` 指向 home 之外（`../` 和绝对路径）时配置被拒绝。Tailscale
  出站只在 `with_gvisor` 构建里有，不带这个标签时 mihomo 拒绝任何 Tailscale 出站，所以 CI 的核心测试另外带
  `with_gvisor` 跑一次这组测试。

**实现**（`internal/engine/mesh.go`，安全项的最后一步）：

- 顶层 `proxies` 和每个 proxy-provider 的 `payload` 里，类型为 `easytier` 的出站：删掉 `proxy-networks`、
  `mapped-listeners`，`enable-exit-node` 写成 false。
- 远程（`http`、`file`）provider 的内容在应用之后才下载，流水线看不到；provider 自己的 `override-expr` 还可以把
  任何出站改成 `easytier` 并打开这些选项。所以这类 provider，以及带 `override-expr` 的内联 provider，在
  `override.override-expr` 末尾追加三条表达式，例如
  `(select(.type == "easytier") | .enable-exit-node) = false`。mihomo 对 provider 读到的每个出站按顺序执行
  这些表达式，追加的最后执行；`exclude-type` 在表达式之前判断，挡不住改类型，所以不用它。测试用 mihomo 自己的
  表达式求值器核对结果。
- 强制项改写后在报告里给出警告 `mesh_restricted`：`entry_id` 是出站的 `mesh:` id（覆写加进来的出站没有 id，
  用它的名称），`target` 是被关闭的选项名，每项一条。只有原来打开的选项才有警告；远程 provider 里的出站改写时
  内核看不到，没有警告。用 `config_yaml` 时没有报告，也就没有警告，但改写照样生效。

## 5. 界面

- **分流页·策略组**：内置、地区、自建之后加"组网"一节，排在"链式出口"之前。每行写名称、类型和来源
  （"Tailscale · 来自「家里」"）。"更多"菜单：
  - "添加常用规则"：对 Tailscale，加一条 IP 段规则（`100.64.0.0/10`、`fd7a:115c:a1e0::/48`，`no-resolve`）和一条
    域名规则（`ts.net`），目标都是这个出站；对 ZeroTier 和 EasyTier，打开"添加规则 · IP 段"，目标预选好，
    地址段由用户填，因为每个网络不同；
  - "使用它的规则"：按目标筛选规则列表。
- **添加规则对话框**：目标下拉框依次是节点选择、自动选择、分隔线、各地区、分隔线、自建策略组、分隔线、
  各组网出站、分隔线、WARP 出口、Tor 出口、VPN Gate 出口、分隔线、直连、拒绝。
- **代理页**：组网出站不是策略组，不在策略组列表里。
- **规则行**：目标已经不存在时，目标后写 `warning` 色的"不存在，已拒绝连接"。
- 文案："组网" Mesh networks。

## 6. 协议

以下字段已写进 `control.proto`（`MeshOutbound`、`CompositionReport.mesh`、`SourceNode.mesh`）和 `host/base.py`，
内核已实现。

- 方案 JSON（routing.md 8.5 节）的 `target` 和 `fallback` 可以写 `mesh:<订阅 id>/<原名>`，`version` 保持 1。
- `CompositionReport` 新增 `mesh` 列表和警告原因 `mesh_missing`；`InspectSource` 的节点新增 `mesh` 标记。
- 第一版不需要新方法。

## 7. 存储与备份

- 方案里的 `mesh:` 引用随方案进入备份。
- 组网出站的凭据（Tailscale 的 `auth-key`、ZeroTier 的 `identity-secret`、EasyTier 的 `network-secret`）第一版
  写在用户自己的配置文件里。订阅下载的内容不进备份，但**本地文件配置的 YAML 原文进备份**（storage.md 第 3 节），
  所以凭据会随快照明文上传。开启备份时的提示加上这一点；M11 改为存系统凭据库，不再进备份。

## 8. 待验证

- EasyTier 不开 `listeners` 时打洞是否仍然可用；据此决定第 4 节的 `listeners` 是强制关闭还是只限制地址。
  v1.19.33 的 EasyTier 出站在 `listeners` 为空时要求写 `peers`（"implicit public.easytier.top is disabled"）。
  实测要做的：在两台不在同一局域网的机器上（一台是开发服务，另一台跑官方 easytier-core），同一个
  `network-name` / `network-secret`，开发服务这边分别用 ① 默认 `listeners`、② `listeners: []` 加 `peers`、
  ③ `listeners` 只写 `127.0.0.1` 的地址加 `peers` 三种写法，看对端的 `easytier-cli peer` 里连接类型是 p2p 还是
  经中继，以及经组网出站访问对端的延迟；同时用 `lsof -i` 看 ① 在哪些地址上监听。②③ 能打洞就把 `listeners`
  强制成 ② 或 ③。
- 订阅自带的配置用 `proxy-providers` 引入的远程列表里有组网出站时，mihomo 如何处理。
- Tailscale 的 MagicDNS 名称在 fake-ip 下能否解析：出站拿到的是域名，要由 tsnet 解析。
- 在国内，Tailscale 的控制面和 DERP 中继可能连不上；是否需要让组网出站经由某个策略组（`dialer-proxy`）连接。
  经由代理后点对点打洞会失效，只能走中继。

## 9. 分期与验收

**第二轮**（随 M9，两边的会话各加一份）：

- 内核：拼装时把组网出站从节点池分出来、`mesh:` 目标、`mesh_missing` 警告、第 4 节的安全项及测试；
- 界面：组网一节、目标下拉框里的组网出站、"添加常用规则"、规则行的提示、备份提示。

验收（经开发服务，不建 TUN）：导入一份含 Tailscale 出站（测试 tailnet 的 auth key）的本地配置 → 它不在
"自动选择"和地区组里 → 用"添加常用规则"加规则后，能经它访问 tailnet 里的一台机器 → 删掉这份配置后，同一规则的
连接被拒绝。

**M11 应用内组网**（首发后）：

- 在应用里添加三种网络，凭据存系统凭据库，不再写进配置文件；
- Tailscale 的交互式登录：内核把登录链接交给界面，界面在浏览器里打开；
- 状态：是否在线、本机的组网地址、对等点数量。这需要新的内核方法，先核实 mihomo 能否取到这些状态；
- 经由：直连（默认）或经某个策略组。
