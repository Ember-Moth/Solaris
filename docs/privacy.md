# 隐私出口：WARP 与 Tor

本文设计两种"隐私出口"：流量先经用户自己的节点，再进入 Cloudflare WARP 或 Tor 网络，最后才到达目标网站。
决策是 [roadmap.md](roadmap.md) 的 D21、D22、D23，实施计划是 M9（WARP）和 M10（Tor）。

状态：设计稿（2026-10-30），未实现。

## 1. 目标与边界

两种出口都是链式代理：用户的节点只作为跳板（"经由"），真正的出口在它后面。

**WARP 出口**：设备 → 机场节点 → Cloudflare WARP → 网站。

| 谁 | 能看到用户的 IP | 能看到访问的网站 |
| --- | --- | --- |
| 运营商 | 能 | 不能 |
| 机场节点 | 能 | 不能，只看到连往 Cloudflare 的加密流量 |
| Cloudflare | 不能，只看到节点 IP | 能 |
| 网站 | 不能，看到的是 Cloudflare 的 IP | — |

没有 WARP 时，机场节点同时知道"谁"和"访问了什么"，网站看到的是节点 IP。加上 WARP 后，这两件事分给了两方，
思路与 iCloud 专用代理相同。代价是信任从机场转到了 Cloudflare。

**Tor 出口**：设备 → 机场节点 → Tor 入口 → 中继 → Tor 出口 → 网站。

| 谁 | 能看到用户的 IP | 能看到访问的网站 |
| --- | --- | --- |
| 运营商 | 能 | 不能 |
| 机场节点 | 能 | 不能，只看到连往 Tor 入口的流量 |
| Tor 入口 | 不能，只看到节点 IP | 不能 |
| Tor 出口 | 不能 | 能（明文协议还能看到内容） |
| 网站 | 不能，看到的是 Tor 出口的 IP | — |

经用户的节点进入 Tor，在国内不需要网桥；还可以访问 `.onion` 网站。

**不做**：

- 不承诺匿名。普通浏览器经 Tor 只隐藏 IP，浏览器指纹和已登录的账户照样能识别用户；界面和文档都只说"隐藏 IP"。
- WARP+ 授权码、Cloudflare Zero Trust 团队账户：第一版不做，账户结构为它们留位置。
- Tor 中继、网桥、洋葱服务的服务端：都不做。

**范围（D21）**：隐私出口只在"我的方案"下提供（[routing.md](routing.md)）。"订阅自带"的配置里策略组名
各不相同，没有可靠的地方挂"经由"；用户在分流页切到"订阅自带"时，隐私出口一节显示"仅在使用我的方案时可用"。

**失败时拒绝（D21）**：出口不可用时（没有 WARP 账户、Tor 没在运行），指向它的规则拒绝连接，不改走普通
代理。地区组没有节点时改走"节点选择"（routing.md 5.3 节）只换了节点；隐私出口悄悄退回普通代理，则正是用户
想避开的情况。

## 2. 用户怎么用

1. 分流页"策略组"标签多一节"隐私出口"，两行："WARP 出口"和"Tor 出口"。"Tor 出口"只在设置页的"实验功能"里
   打开后才出现。
2. 点"WARP 出口"打开设置对话框，打开开关。隧道已连接时，内核立刻经"经由"组注册账户（3.2 节）；未连接时提示
   "连接后注册"，连接后自动完成。
3. 添加规则时，目标下拉框里多两项"WARP 出口""Tor 出口"，排在策略组之后、"直连"之前。例如"OpenAI → WARP 出口"。
   想让所有代理流量都经 WARP，就把"其他流量"的目标改成"WARP 出口"。
4. 规则列表、连接页按普通目标显示；出口不可用时，规则行目标后写 `warning` 色的"不可用，已拒绝连接"。

**出口设置对话框**：

| 项 | WARP 出口 | Tor 出口 |
| --- | --- | --- |
| 开关 | 启用 | 启用 |
| 经由 | 策略组下拉框，默认"节点选择"。WARP 的出口 IP 可能靠近入口，也就是经由节点所在的地区（待验证，第 8 节） | 同左 |
| 协议 | MASQUE（HTTP/2）/ MASQUE（HTTP/3）/ WireGuard，默认 MASQUE（HTTP/2），见 3.1 节 | — |
| 状态 | 账户（已注册的时间、设备 id 的前 8 位）、上次注册的错误 | Tor 网络连接进度、出口线路数 |
| 操作 | 重新注册、删除账户 | 清除 Tor 数据 |
| 说明 | "Cloudflare 能看到你访问的网站；机场节点看不到" | "只隐藏 IP，不等于匿名；速度较慢；只支持 TCP；部分网站会拦截 Tor" |

"经由"属于方案（随方案进入备份）；协议、账户和 Tor 开关属于本机。

## 3. WARP 出口

### 3.1 链路与协议

WARP 出口是 mihomo 的一个出站，`dialer-proxy` 指向"经由"组。mihomo v1.19.33 已有所需的一切：

- `masque` 出站：默认值就是 Cloudflare 消费者版 WARP（SNI `consumer-masque.cloudflareclient.com`）。
  `network: h2` 时走 HTTP/2，底层是 TCP；不写时走 HTTP/3，底层是 QUIC。
- `wireguard` 出站：需要 `reserved`（由注册返回的 `client_id` 算出）。
- 任何出站都可以写 `dialer-proxy: <出站或策略组名>`，自己的连接经那个出站发出（`proxydialer.NewByName`）。

| 协议 | 对"经由"节点的要求 | 说明 |
| --- | --- | --- |
| MASQUE（HTTP/2） | 只要 TCP | **默认**。任何节点都能当跳板 |
| MASQUE（HTTP/3） | 节点要能转发 UDP | 开销比 HTTP/2 小，没有队头阻塞 |
| WireGuard | 节点要能转发 UDP | 开销最小 |

不少机场节点不转发 UDP，或只转发到特定端口，所以默认用只需要 TCP 的 HTTP/2。

**DNS**：出站开启 `remote-dns-resolve`，`dns` 是 `1.1.1.1` 和 `2606:4700:4700::1111`。经 WARP 的域名在 WARP
隧道里解析，节点看不到 DNS 查询。

**其他**：
- MTU 1280；`udp: true`。
- WARP 提供 IPv6 出口，节点没有 IPv6 也能访问 IPv6 站点。
- 延迟测试照常：mihomo 经整条链路访问测速地址。

### 3.2 注册与账户

注册由内核完成：新方法 `RegisterWarp`（6.2 节）用 Go 的 HTTP 客户端，连接经"经由"组发出，所以：

- Cloudflare 只看到节点 IP，看不到用户的真实 IP；
- 在国内也能连上 `api.cloudflareclient.com`。

所以注册需要内核在运行，"经由"组在运行中的配置里。

**流程**（与 warp 仓库 `api.sh` 的做法一致，接口不是官方公开的）：

1. 生成密钥：MASQUE 用 ECDSA P-256（`key_type: secp256r1`、`tunnel_type: masque`），WireGuard 用 X25519。
2. `POST /v0a<版本>/reg`，带公钥、随机的 `install_id` 和 `fcm_token`、`tos` 时间、`locale`。
3. `PATCH /v0a<版本>/reg/<id>`，`{"warp_enabled": true}`。注册返回时 WARP 默认关闭，不打开就连不通。
4. 从返回里取：设备 `id` 和 `token`、对端公钥、端点、隧道内的 IPv4 / IPv6 地址、`client_id`
   （WireGuard 的 `reserved` 是它 base64 解码后的 3 个字节）。

- 在 MASQUE 和 WireGuard 之间切换不重新注册：`PATCH /reg/<id>` 换成另一种密钥。
- 接口路径里的版本号、`CF-Client-Version`、`User-Agent` 集中在 `internal/warp` 的常量里。Cloudflare 改动后
  只改这一处。
- 测试只对本机的假服务器，CI 从不访问真实接口。

**错误**：返回 `operation_failed`，`message` 写 Cloudflare 的原文。限流（HTTP 429，或正文 `error code: 1015`）
单独说明："Cloudflare 限制了注册频率，稍后再试，或换一个经由的节点"。许多用户共用同一个节点 IP 时容易遇到。

**保存**：

- 账户（设备 id、token、私钥、对端公钥、端点、地址、`client_id`、协议）是凭据，存系统凭据库（D14 的 `keyring`），
  不进数据库，也不进备份快照。新设备自己注册一个账户。
- 每次 Start / Reload 时，界面把账户放进 `Composition`（6.1 节）交给内核。内核只在内存里用它。例外是 Windows
  服务为开机恢复保存的运行副本（architecture.md §5.1），它在只有 SYSTEM 和属主能读的目录里。
- 删除账户只删除本机保存的副本。

### 3.3 拼装

内核拼装时（routing.md 8.1 节），方案里有任何规则指向 `egress:warp`，并且 `Composition` 带了账户，就生成：

```yaml
proxies:
  - name: WARP 出口          # 按 locale，英文为 "WARP exit"
    type: masque             # WireGuard 时为 wireguard，并写 reserved、端口
    server: <注册返回的端点>
    port: 443
    private-key: <私钥>
    public-key: <对端公钥>
    ip: <隧道内 IPv4>
    ipv6: <隧道内 IPv6>
    network: h2              # HTTP/3 时不写
    mtu: 1280
    udp: true
    remote-dns-resolve: true
    dns: [1.1.1.1, 2606:4700:4700::1111]
    dialer-proxy: <经由组的名称>
```

- 规则目标 `egress:warp` 展开成这个出站的名称。
- **没有账户时**：指向它的规则改写为 `REJECT`，报告里给出警告 `egress_unavailable`（D21）。
- **经由组没有节点时**：经由改走"节点选择"，报告里给出警告 `via_empty`。这只换了跳板，不影响隐私性质。
- 出站名称和"Tor 出口"一样是保留名称：自建组不能用，重名的节点按 routing.md 8.1 节加后缀。
- 覆写、平台补丁和安全项照常在拼装之后应用。

### 3.4 风险

| 风险 | 应对 |
| --- | --- |
| 注册接口不是官方的，Cloudflare 可能改动、限流或要求新的校验 | 常量集中一处，出错时界面写明原因；功能失败时按 D21 拒绝连接，不会退回普通代理 |
| Cloudflare 能看到用户访问的网站 | 设置对话框写明；用户按规则选哪些流量经 WARP |
| 部分服务拦截 WARP 的 IP，也有服务靠它解锁 | 按规则选目标，而不是只有全局开关 |
| 机场的审计或使用条款可能不允许这类流量 | 文档提示；功能默认关闭 |
| 同一账户在两条链路上同时使用，WireGuard 会在两个端点间来回切换 | 每个方案只有一个 WARP 出口和一个"经由" |

## 4. Tor 出口（实验功能）

### 4.1 链路

```text
应用 → mihomo 规则 → "Tor 出口"（socks5 出站，127.0.0.1:P1，随机账号密码）
     → arti（界面进程，solaris_tor）→ arti 的 TCP 连接
     → mihomo 的 tor-upstream 入站（127.0.0.1:P2，随机账号密码，proxy 固定为经由组）
     → 机场节点 → Tor 入口 → 中继 → Tor 出口 → 网站
```

- arti-client 没有"上游代理"配置，但运行时可以替换 TCP 连接的实现（tor-rtcompat 的 `with_tcp_provider`）。
  `solaris_tor` 的实现只做一件事：对 P2 发 SOCKS5 CONNECT。arti 不解析 DNS，也不用 UDP，所以它
  **没有任何直连流量**；有测试守住这一点。
- `tor-upstream` 入站用 mihomo listeners 的 `proxy:` 固定到经由组，不经过规则，所以不会绕回 Tor 出口。
- 两个端口都只监听回环地址，都要求随机生成的账号密码（RFC 1929）。随机值每次界面启动重新生成。
  Windows 上同一台机器的其他用户也能连回环端口，所以账号密码是必需的。
- Tor 只能传 TCP。UDP 连接（例如 QUIC）必须失败，让应用回落到 TCP。mihomo 匹配规则时，遇到不支持 UDP 的出站会
  **跳过这条规则、继续往下匹配**（`tunnel.match`），UDP 会落到后面的规则，经普通代理出去。所以"Tor 出口"的
  socks5 出站写 `udp: true`，由 `solaris_tor` 的 SOCKS5 服务对 UDP ASSOCIATE 回复"不支持的命令"，连接在 Tor
  出口这里失败，不再往下匹配。

### 4.2 `solaris_tor` 模块

arti 用 pyo3 做成 Python 扩展模块，在界面进程里运行（D23）。不放进 Go 内核，原因是：

- **内核不碰 Rust**：Windows 服务不需要 cgo 和 mingw；macOS 系统扩展不需要再链接一个 Rust 静态库。Rust 工具链
  只进 Python 的构建。扩展模块用 MSVC 目标，与 Windows 上的 Python 一致；`abi3`，不随 Python 小版本重编。
- **权限更低**：arti 要解析大量来自中继的数据。放在普通用户权限的界面进程里，而不是 SYSTEM 服务或以 root
  运行的系统扩展里。
- **出问题不影响隧道**：arti 崩溃或卡住，只影响走 Tor 的流量。

源码在 `src/tor/`（Rust crate，maturin 构建），钉住 arti-client 0.47（2026-10-01，MIT OR Apache-2.0，可以并入
GPL-3.0）。接口很小：

| Python 接口 | 作用 |
| --- | --- |
| `TorService(state_dir, cache_dir)` | 创建；arti 运行在模块自己的 tokio 线程上，不占用 Python 的全局锁 |
| `start(upstream_port, username, password) -> (port, username, password)` | 启动本机 SOCKS5 服务和 Tor 客户端，返回 P1 及其账号密码 |
| `stop()` | 关闭 SOCKS5 服务和全部线路 |
| `status() -> dict` | 阶段（未运行、连接中、已就绪、出错）、进度百分比、错误信息 |

- **SOCKS5 服务**：只支持 CONNECT，UDP ASSOCIATE 回复"不支持的命令"（4.1 节）。目标可以是域名（fake-ip 下
  mihomo 交来的是域名）或 IP，交给 `TorClient::connect`。
- **线路隔离**：按目标主机隔离，同一主机共用线路，不同主机不共用，相当于 C tor 的 `IsolateDestAddr`。所有流量
  都来自 mihomo 的同一组账号密码，不隔离的话，所有网站会共用一条线路，彼此可以关联。开销需要实测（第 8 节）。
- **`.onion`**：开启 arti 的 `onion-service-client` 特性。服务清单加一项"Onion 网站"（`DOMAIN-SUFFIX onion`），
  建议目标"Tor 出口"。fake-ip 下 `.onion` 不会发出真实的 DNS 查询。
- **数据目录**：用户数据目录下的 `tor/`（状态和目录缓存），不进备份；设置对话框可以清除。

### 4.3 生命周期

- Tor 开关打开、隧道在运行，界面就启动 `TorService`；隧道停止或开关关闭时停止它。
- **顺序**：
  1. 界面选两个空闲的回环端口和两组随机账号密码；
  2. 放进 `Composition`，由 Start / Reload 交给内核，内核生成 `tor-upstream` 入站和"Tor 出口"出站；
  3. 应用成功后，界面启动 arti。

  arti 连上 Tor 网络前，经 Tor 的连接会失败。P2 被占用时内核返回 `apply_failed`，界面换一个端口再试一次。
- **退出界面**：arti 随界面进程结束，经 Tor 的连接被拒绝。这是失败即断开，不会退回直连或普通代理。界面打开
  Tor 时提示"退出界面后 Tor 不可用"。关闭窗口默认留在托盘，界面进程仍在，不受影响。
- **重新打开界面**：隧道还在运行时，界面重新生成端口和账号密码，Reload 一次后启动 arti。
- **macOS**：系统扩展里的 mihomo 连界面进程的回环端口，界面进程连扩展里 mihomo 的回环端口。回环流量不进隧道，
  开启 `includeAllNetworks`（D19）时也是；需要在测试 Mac 上确认（第 8 节）。

### 4.4 拼装

方案里有规则指向 `egress:tor`，并且 `Composition` 带了 Tor 端点，内核生成：

```yaml
proxies:
  - {name: Tor 出口, type: socks5, server: 127.0.0.1, port: P1, username: …, password: …, udp: true}
listeners:
  - {name: tor-upstream, type: socks, listen: 127.0.0.1, port: P2, udp: false,
     users: [{username: …, password: …}], proxy: <经由组的名称>}
```

- 没有 Tor 端点时，与 WARP 一样改写为 `REJECT` 并警告 `egress_unavailable`。
- 安全项照常检查入站：`tor-upstream` 只监听回环地址，与 `allow_lan` 无关；它不是 `tun`、`redir`、`tproxy`
  类型，不会被删掉（architecture.md §3.1）。
- IP 类规则（`GEOIP`、`IP-CIDR` 不带 `no-resolve`）会先用常规 DNS 解析域名，这次查询不经 Tor。
  界面在目标是"Tor 出口"的规则上默认勾选 `no-resolve`，并在帮助文字里说明。

### 4.5 风险

| 风险 | 应对 |
| --- | --- |
| 用户以为经 Tor 就是匿名 | 只用"隐藏 IP"的说法；打开时提示浏览器指纹和登录状态的问题，推荐需要匿名时用 Tor Browser |
| 慢、只支持 TCP、很多网站拦截 Tor 出口或频繁弹验证码 | 实验功能，默认关闭；按规则选流量，不建议设为"其他流量"的目标 |
| arti-client 每月发一个不兼容版本 | 钉住版本，每季度升级一次；有安全公告时立即升级 |
| 安装包体积和界面进程的内存增加 | M10 实测后写进本文；超出预期就改成按需下载的扩展模块 |
| 依赖界面进程 | 失败即断开；托盘常驻；界面状态行写明 |
| 当地法律和机场条款 | 文档提示，由用户自行判断 |

## 5. 界面

- **分流页·策略组**：内置、地区、自建之后加"隐私出口"一节，两行，每行写出口名称、经由、状态
  （"已就绪"、"正在连接 Tor 网络 45%"、"未注册"、"不可用：…"）。点行打开 2 节的设置对话框。
- **添加规则对话框**：目标下拉框是节点选择、自动选择、分隔线、各地区、分隔线、自建策略组、分隔线、WARP 出口、
  Tor 出口、分隔线、直连、拒绝。未启用的出口不可选，注明"未启用"。
- **设置页**：新分组"实验功能"，一项"Tor 出口"（本机设置 `tor_enabled`，默认关闭）；打开时显示 4.5 节第一行
  的提示。WARP 的协议是本机设置 `warp_protocol`，在出口设置对话框里改，不在设置页重复。
- **代理页**：两个出口不是策略组，不在策略组列表里；延迟在分流页的出口行里测。
- **连接页**：链路一列显示 mihomo 报告的出站链；经 `dialer-proxy` 的连接怎样显示，M9 实现时核实。
- 文案先写中文，再在 `en.ts` 里补英文："隐私出口" Privacy exits，"WARP 出口" WARP exit，"Tor 出口" Tor exit，
  "经由" Via。

## 6. 协议与内核

### 6.1 `Composition` 与方案 JSON

- `Composition` 新增 `egress`：
  - `warp`：账户（3.2 节的各项）和协议；
  - `tor`：P1 及其账号密码、P2 及其账号密码。

  两者都可以不带，不带就是不可用。
- 方案 JSON（routing.md 8.5 节）新增 `egress`，只放经由：

  ```json
  "egress": {"warp": {"via": "proxy"}, "tor": {"via": "region:JP"}}
  ```

  `via` 是策略组 id，默认 `proxy`。规则目标和兜底目标可以写 `egress:warp`、`egress:tor`。字段是新增的，
  `version` 保持 1。
- `CompositionReport` 的警告新增两种原因：`egress_unavailable`、`via_empty`。

### 6.2 新增方法

| 方法 | 作用 |
| --- | --- |
| `RegisterWarp` | 请求带经由组名、协议，以及可选的已有账户（只有设备 id 和 token）。不带已有账户就注册新设备；带了就为这台设备换密钥。返回完整账户。需要内核在运行，否则 `not_running` |

- 字段号用 55（51 到 54 已留给 M8b）。
- 两种宿主都提供；模拟宿主返回一个固定的假账户。
- 实现放在 `src/core/internal/warp`。HTTP 客户端的连接经 `proxydialer.NewByName(经由组, tunnel)` 发出，超时 30 秒。

## 7. 存储与备份

| 数据 | 存放 | 进备份 |
| --- | --- | --- |
| 方案里的 `egress.*.via` | `plans`（方案 JSON 的一部分） | 是 |
| WARP 账户 | 系统凭据库 | 否，新设备重新注册 |
| `warp_protocol`、`tor_enabled` | `settings`，本机范围 | 否 |
| Tor 状态与目录缓存 | 用户数据目录下的 `tor/` | 否 |

恢复到新设备后，方案里指向隐私出口的规则先按 D21 拒绝连接；分流页的隐私出口一节提示"注册 WARP 账户"或
"打开 Tor 出口"。

## 8. 待验证

- 经一个只有 TCP 的节点，mihomo 的 MASQUE（HTTP/2）能否连上 WARP，延迟和吞吐各是多少。
- WARP 的出口 IP 是否随经由节点的地区变化。
- 注册接口当前的版本号；同一个节点 IP 上的注册限流。
- 连接页对 `dialer-proxy` 链路的显示。
- `solaris_tor` 的体积，界面进程增加的内存，经节点首次连上 Tor 网络的时间，按目标主机隔离线路的开销。
- macOS：系统扩展与界面进程互连回环端口，包括开启 `includeAllNetworks` 时。

## 9. 分期与验收

**M9 WARP 出口**（首发前，阶段 D）

- 内核：`internal/warp` 的注册、`RegisterWarp`、拼装 `egress`、两种警告。
- 界面：隐私出口一节、出口设置对话框、规则目标、账户存取（`keyring`）、模拟宿主。

两部分改动的文件不重叠，可以并行。

验收：
1. 用一个只有 TCP 的真实节点连接，打开 WARP 出口，注册成功；
2. 添加"ifconfig.me → WARP 出口"，访问得到的是 Cloudflare 的 IP，而不是节点 IP；
3. 删除账户后，同一规则的连接被拒绝，不会经节点直接出去；
4. 协议切到 WireGuard（节点支持 UDP 时），结果相同。

**M10 Tor 出口**（首发后，实验功能）

- `src/tor` 的 pyo3 模块：上游 TCP 实现、SOCKS5 服务、线路隔离、状态；
- 构建：maturin 进 Python 的 CI 和两个平台的打包；
- 界面：实验功能开关、生命周期、出口行的进度；
- 内核：拼装 `tor-upstream` 入站和 Tor 出口；
- 服务清单加"Onion 网站"。

验收：
1. 打开 Tor 出口，一分钟内连上 Tor 网络；
2. 经 Tor 的连接，check.torproject.org 显示正在使用 Tor；
3. 能打开一个 `.onion` 网站；
4. 退出界面后，同一规则的连接被拒绝；
5. 抓包确认 arti 没有直连流量。
