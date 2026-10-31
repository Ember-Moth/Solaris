# 决策

本文记录 Solaris 的设计决策。代码注释和其他文档按编号引用它们（如"D19"），所以编号不变、不重用；新的决策从 D34
接着编号。每条只写结论，理由和细节在"详见"一栏的文档里。

## 待决策

目前没有。

## 已决定

| # | 决定 | 详见 |
| --- | --- | --- |
| D1 | 产品名与应用名都是 Solaris。GitHub 仓库为 `Ember-Moth/Solaris`，macOS bundle id 前缀 `dev.tschen.solaris`；产品标识集中在 `src/core/internal/product/product.go` 与 `src/solaris/product.py` | [architecture.md](architecture.md) |
| D2 | Windows 只做 x64，macOS 只做 arm64 | [architecture.md](architecture.md) §6.1、[release.md](release.md) §1 |
| D3 | Windows 服务带 `with_gvisor` 构建，四种 TUN 栈都提供，由用户在设置里选择；macOS 系统扩展同样带 `with_gvisor`，fd 模式默认 gVisor 栈 | [architecture.md](architecture.md) §3.4 |
| D4 | 覆写只做 Merge，不做 Script；一份全局覆写，加上每份订阅各一份 | [ui.md](ui.md) §4、[architecture.md](architecture.md) §3.2 |
| D5 | 属主注销时停止隧道，下次登录后恢复 | [architecture.md](architecture.md) §5.4 |
| D5b | Windows 上别的用户的隧道在运行时，管理员经 UAC 确认后可以接管：停掉对方的隧道、写回对方的系统代理，自己成为属主；不是管理员只能等对方断开或注销。界面经 UAC 启动服务自己的接管辅助进程（`solaris-service.exe -take-over`），服务凭客户端 token 里启用的 Administrators 组认定管理员 | [architecture.md](architecture.md) §5.4、[ui.md](ui.md) §8 |
| D6 | 订阅一键导入注册 `solaris://install-config?url=…` 和 `clash://install-config` | [release.md](release.md) §7 |
| D7 | macOS 的配置和 Windows 一样，经控制协议的 `Start` / `Reload` 传入，不经 `startVPNTunnel(options:)` | [architecture.md](architecture.md) §6.2 |
| D8 | 不给 provider message 分片：配置照旧用一条消息经 `Start` / `Reload` 发给系统扩展。单条消息的上限在测试 Mac 上用大配置核实，真带不动时再重新决定 | [architecture.md](architecture.md) §6.3 |
| D9 | 提供深色主题；深色的颜色先在 DESIGN.md 里定义，再由它生成 | [ui.md](ui.md) §6、[DESIGN.md](../DESIGN.md)"深色" |
| D10 | 许可证：GPL-3.0-only | [LICENSE](../LICENSE) |
| D11 | 内置检查更新与下载：安装包来自 GitHub Releases，校验后由用户确认安装 | [release.md](release.md) §2、§3 |
| D12 | 界面提供简体中文和英文，中文为源语言 | [ui.md](ui.md) §5 |
| D13 | 本地用 SQLite；云端只做备份与恢复，不做双向同步；快照不加密。iCloud 用 CloudKit，仅 macOS；Google Drive 在应用内登录、用 appDataFolder，只申请 `drive.appdata` | [storage.md](storage.md) |
| D14 | Google 令牌存放在系统凭据库（macOS 钥匙串、Windows 凭据管理器），用 `keyring`。WARP 账户和应用内组网的凭据也这样存放，都不进数据库和备份 | [storage.md](storage.md) §4、[exits.md](exits.md) §3.2、[mesh.md](mesh.md) §10.2 |
| D15 | 自定义分流：全局开关"订阅自带 / 我的方案"，首次导入时自动选择；"我的方案"下所有订阅组成节点池，可单独排除；可以有多套方案；规则条目按意图记录，默认按服务添加；规则类型为 GEOSITE / GEOIP、域名、IP 段 | [routing.md](routing.md) |
| D16 | 分流配置在内核里拼装，节点筛选和规则匹配都用 mihomo 自己的逻辑 | [routing.md](routing.md) §8.1 |
| D17 | GEO 分类列表由内核从正在使用的 GEO 数据里读取；安装包附带初始 GEO 数据，另附一份分类名清单作兜底 | [routing.md](routing.md) §8.4 |
| D18 | GEO 数据全量随安装包附带（geosite 约 4.4 MB、geoip 约 16.5 MB） | [routing.md](routing.md) §8.4 |
| D19 | macOS 扩展的 `includeAllNetworks` 默认关闭，用户可在设置页打开；打开后 `system`、`mixed` 栈换成 gVisor | [architecture.md](architecture.md) §3.4、[ui.md](ui.md) §8 |
| D20 | fd 模式的 `dns.fake-ip-range` 固定为 `198.18.0.1/16`，TUN 地址由它得出；用户关掉 `ipv6` 时，进入隧道的 IPv6 流量被丢弃 | [architecture.md](architecture.md) §3.1 |
| D21 | 链式出口只在"我的方案"下提供；出口不可用时，指向它的规则拒绝连接，不改走普通代理 | [exits.md](exits.md) §1 |
| D22 | WARP 出口放在内核：mihomo 的 MASQUE 或 WireGuard 出站，`dialer-proxy` 经用户的节点，默认 MASQUE over HTTP/2；内核经节点注册账户，账户存系统凭据库、不进备份 | [exits.md](exits.md) §3 |
| D23 | Tor 出口用 arti-client，经 pyo3 运行在界面进程，作为实验功能；退出界面后 Tor 不可用，连接被拒绝（未实现） | [exits.md](exits.md) §4 |
| D24 | 保留 Tailscale、ZeroTier、EasyTier 出站；它们不进节点池和任何策略组，作为"组网"出站单独列出、供规则引用；只用作出站，安全项关闭 EasyTier 对外提供出口和子网的选项 | [mesh.md](mesh.md) |
| D25 | VPN Gate 作为第三种链式出口：经节点拉列表，只用 TCP 的 OpenVPN 服务器，按国家和评分挑选；默认负载均衡组按网站分配服务器并跳过失效的，也可选只用一台；定位是换 IP 和住宅 IP，不宣传隐私 | [exits.md](exits.md) §5 |
| D26 | 界面上只有一个连接开关（概览页出站分组第一行）；连接时怎样接管流量由设置页"接管方式"决定，TUN 和系统代理默认都开；macOS 上 TUN 是必选的 | [DESIGN.md](../DESIGN.md)"连接开关"、[ui.md](ui.md) §8 |
| D27 | 订阅由 Python 下载、Go 内核解析和校验，只此一份解析。没有内核能应答时（macOS 隧道断开）先存下原文，连接后、`Start` 之前再解析和校验；界面在没有内核时隐藏需要它的部分 | [ui.md](ui.md) §3 |
| D28 | 最低支持 macOS 26：PySide6 保持 6.12，不为 macOS 15 降到 6.11 | [apple/README.md](../apple/README.md)"组装 Solaris.app" |
| D29 | Windows 安装包不签名：服务对客户端只做文件身份校验，检查更新只核对 SHA-256；签名方案定下后再接上签名者常量和 `SignedRelease` | [release.md](release.md) §3、[architecture.md](architecture.md) §5.3 |
| D30 | 设置页不为备份不加密单独提示：导出、开启 iCloud、登录 Google Drive 都不弹确认框，各行的说明里也不写"不加密"；备份不加密这一事实写在隐私说明里 | [storage.md](storage.md) §3、[ui.md](ui.md) §8、[PRIVACY.md](../PRIVACY.md) |
| D31 | engine 的方法直接返回控制协议的 protobuf 消息（`controlpb`），从 mihomo 类型的映射留在 engine，分发器不再映射，也不另设一套对应的 Go 类型 | [architecture.md](architecture.md) §3.2 |
| D32 | 设置页「延迟测试」可选 HTTP（默认）或 TCP 握手，只用于用户发起的测速和界面显示的结果；自动选择、故障转移、负载均衡组照旧按 mihomo 的 URL 测试选择。TCP 握手由内核像 mihomo 的出站一样直接拨节点的服务器，不进 TUN；结果存在 engine，应用下一份配置时清空。没有 TCP 服务器可拨的不测、显示「—」：走 UDP 或可能走 UDP 的类型、经 `dialer-proxy` 的节点、组网出站、直连和拒绝；组显示它此刻所用节点的结果 | [architecture.md](architecture.md) §4.3、[ui.md](ui.md) §8 |
| D33 | 请求预算覆盖连接、排队、发送与执行，UI > 宿主 > 内核；排队可取消，已进入内核的事务保持自身期限。不可中断的写操作超时后停止接收新操作并回收宿主进程，不假装取消或并行启动新内核。显式断开先保留意图，阻止迟到操作和自动恢复重新连接 | [architecture.md](architecture.md) §4.1.1 |
