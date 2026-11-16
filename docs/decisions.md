# 决策

本文记录 Solaris 的设计决策。代码注释和其他文档按编号引用它们（如"D19"），所以编号不变、不重用；新的决策从 D46
接着编号。每条只写结论，理由和细节在"详见"一栏的文档里。

## 待决策

目前没有。

## 已决定

| # | 决定 | 详见 |
| --- | --- | --- |
| D1 | 产品名与应用名都是 Solaris。GitHub 仓库为 `Ember-Moth/Solaris`，macOS bundle id 前缀 `dev.tschen.solaris`；产品标识集中在 `src/core/internal/product/product.go` 与 `src/solaris/product.py` | [architecture.md](architecture.md) |
| D2 | macOS 提供 arm64 + x86_64 通用 DMG，UI、Python/Qt、管理库和扩展均含两个架构；Windows 提供 x64，并按 D40 扩展到 ARM64 | [architecture.md](architecture.md) §6.1、[release.md](release.md) §1 |
| D3 | Windows 服务带 `with_gvisor` 构建，四种 TUN 栈都提供，由用户在设置里选择，两个平台都默认 `mips`；macOS 系统扩展同样带 `with_gvisor`，"跟随配置"而配置没写栈时 fd 模式用 gVisor 栈 | [architecture.md](architecture.md) §3.4 |
| D4 | 覆写只做 Merge，不做 Script；一份全局覆写，加上每份订阅各一份 | [ui.md](ui.md) §4、[architecture.md](architecture.md) §3.2 |
| D5 | 属主注销时停止隧道，下次登录后恢复 | [architecture.md](architecture.md) §5.4 |
| D5b | Windows 上别的用户的隧道在运行时，管理员经 UAC 确认后可以接管：停掉对方的隧道、写回对方的系统代理，自己成为属主；不是管理员只能等对方断开或注销。界面经 UAC 启动服务自己的接管辅助进程（`solaris-service.exe -take-over`），服务凭客户端 token 里启用的 Administrators 组认定管理员 | [architecture.md](architecture.md) §5.4、[ui.md](ui.md) §8 |
| D6 | 订阅一键导入注册 `solaris://install-config?url=…` 和 `clash://install-config`（另见 D45） | [release.md](release.md) §7 |
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
| D19 | macOS 扩展的 `includeAllNetworks` 默认关闭，用户可在设置页打开；打开后 `system`、`mixed` 栈换成 `gvisor` | [architecture.md](architecture.md) §3.4、[ui.md](ui.md) §8 |
| D20 | fd 模式的 `dns.fake-ip-range` 固定为 `198.18.0.1/16`，TUN 地址由它得出；用户关掉 `ipv6` 时，进入隧道的 IPv6 流量被丢弃 | [architecture.md](architecture.md) §3.1 |
| D21 | 链式出口只在"我的方案"下提供；出口不可用时，指向它的规则拒绝连接，不改走普通代理 | [exits.md](exits.md) §1 |
| D22 | WARP 出口放在内核：mihomo 的 MASQUE 或 WireGuard 出站，`dialer-proxy` 经用户的节点，默认 MASQUE over HTTP/2；内核经节点注册账户，账户存系统凭据库、不进备份 | [exits.md](exits.md) §3 |
| D23 | Tor 出口用 arti-client，经 pyo3 运行在界面进程，作为实验功能；界面进程不在时（如崩溃）Tor 不可用，连接被拒绝（未实现） | [exits.md](exits.md) §4 |
| D24 | 保留 Tailscale、ZeroTier、EasyTier 出站；它们不进节点池和任何策略组，作为"组网"出站单独列出、供规则引用；只用作出站，安全项关闭 EasyTier 对外提供出口和子网的选项 | [mesh.md](mesh.md) |
| D25 | VPN Gate 作为第三种链式出口：经节点拉列表，只用 TCP 的 OpenVPN 服务器，按国家和评分挑选；默认负载均衡组按网站分配服务器并跳过失效的，也可选只用一台；定位是换 IP 和住宅 IP，不宣传隐私 | [exits.md](exits.md) §5 |
| D26 | 界面上只有一个连接开关（概览页出站分组第一行）；连接时怎样接管流量由设置页"接管方式"决定，Windows 全新安装默认开启 TUN、关闭系统代理；macOS 上 TUN 是必选的 | [DESIGN.md](../DESIGN.md)"连接开关"、[ui.md](ui.md) §8 |
| D27 | 订阅由 Python 下载、Go 内核解析和校验，只此一份解析。没有内核能应答时（macOS 隧道断开）先存下原文，连接后、`Start` 之前再解析和校验；界面在没有内核时隐藏需要它的部分 | [ui.md](ui.md) §3 |
| D28 | 最低支持 macOS 26：PySide6 保持 6.12，不为 macOS 15 降到 6.11 | [apple/README.md](../apple/README.md)"组装 Solaris.app" |
| D29 | **已被 D42 取代。** 1.0.0 的 Windows 安装包不签名：服务对客户端只做文件身份校验，检查更新只核对 SHA-256。已安装的 1.0.0 没有签名者常量，照常接受签了名的安装包 | [release.md](release.md) §3、[architecture.md](architecture.md) §5.3 |
| D30 | 设置页不为备份不加密单独提示：导出、开启 iCloud、登录 Google Drive 都不弹确认框，各行的说明里也不写"不加密"；备份不加密这一事实写在隐私说明里 | [storage.md](storage.md) §3、[ui.md](ui.md) §8、[PRIVACY.md](../PRIVACY.md) |
| D31 | engine 的方法直接返回控制协议的 protobuf 消息（`controlpb`），从 mihomo 类型的映射留在 engine，分发器不再映射，也不另设一套对应的 Go 类型 | [architecture.md](architecture.md) §3.2 |
| D32 | 设置页「延迟测试」可选 HTTP（默认）或 TCP 握手，只用于用户发起的测速和界面显示的结果；自动选择、故障转移、负载均衡组照旧按 mihomo 的 URL 测试选择。TCP 握手由内核像 mihomo 的出站一样直接拨节点的服务器，不进 TUN；结果存在 engine，应用下一份配置时清空。没有 TCP 服务器可拨的不测、显示「—」：走 UDP 或可能走 UDP 的类型、经 `dialer-proxy` 的节点、组网出站、直连和拒绝；组显示它此刻所用节点的结果 | [architecture.md](architecture.md) §4.3、[ui.md](ui.md) §8 |
| D33 | 请求预算覆盖连接、排队、发送与执行，UI > 宿主 > 内核；排队可取消，已进入内核的事务保持自身期限。不可中断的写操作超时后停止接收新操作并回收宿主进程，不假装取消或并行启动新内核。显式断开先保留意图，阻止迟到操作和自动恢复重新连接 | [architecture.md](architecture.md) §4.1.1 |
| D34 | Windows 睡眠与唤醒通知按轮次合并，恢复仅针对仍登录的原属主及其连接意图；手动断开、注销和换属主取消恢复，最多五次尝试且整轮不超过一分钟。自动恢复先写保护标记，进程中断后等待手动重试，避免 SCM 崩溃循环 | [architecture.md](architecture.md) §5.2 |
| D35 | macOS 扩展停用是显式维护动作：先断开所有本应用隧道，再请求系统停用，确认完成后仅清理 Solaris 的 VPN 配置，保留订阅、设置与凭据。待处理不显示完成，允许重试；下次连接重新激活。维护与连接、配置修改串行，预算包括排队 | [architecture.md](architecture.md) §9、[ui.md](ui.md) §8 |
| D36 | macOS 系统提示中项目控制的扩展名称与用途说明由 InfoPlist.strings 提供中英文，英文回退，按系统语言偏好解析；应用内指引按应用语言。构建检查最终扩展的语言资源，不改变系统按钮 | [ui.md](ui.md) §5 |
| D37 | 崩溃记录仅本地保存，Python 未处理异常与致命堆栈、Go 运行时输出、Windows 按进程配置的小型转储各有来源。普通诊断仅含概况，原始内容单独确认导出。保持用户和权限边界，不为日志连接 VPN 或提权；受保护的系统扩展及服务原生报告由管理员提取。保留匹配构建符号，日志与导出均有数量、时间及空间限制 | [PRIVACY.md](../PRIVACY.md)、[release.md](release.md) §4 |
| D38 | mihomo 增强版通过 replace 固定到 Ember-Moth/mihomo 的明确提交，版本以 src/core/go.mod 为准，保留上游导入路径；发布构建启用 Naive，Windows 携带并限定加载同版 Cronet DLL，macOS 静态合并同版 Cronet。AnyTLS+REALITY、REALITY 握手版本及 Snell v4–v6 行为采用该增强版（分支的基线和 Snell 部分已被 D44 取代）；升级时同步原生模块、版本标识与许可证清单并运行协议回归 | [architecture.md](architecture.md) §3.4 |
| D39 | Windows 安装器使用自定义 Win32 UI，沿用 Solaris 设计规范；NSIS 保留为安装事务引擎。首次安装可选经权限检查的本地目录，升级沿用已有位置；交互安装/升级需主动同意随包 GPLv3 原文。保留 `/S`、`/PURGE`、`/UPGRADE` 和普通权限启动应用的行为。卸载默认取消且默认保留数据，执行事务期间禁止中途关闭 | [architecture.md](architecture.md) §9、[DESIGN.md](../DESIGN.md) |
| D40 | Windows 11 ARM64 使用原生 ARM64 GUI、Go 服务、Cronet 与 Wintun；NSIS 引导程序及其 UI 插件仍是 x86。构建按架构隔离并检查 PE，更新按原生 OS 架构选择安装包，x64 到 ARM64 原位升级保留数据；CI 在原生 ARM runner 检查安装与迁移 | [architecture.md](architecture.md) §10、[release.md](release.md) §1 |
| D41 | 退出 Solaris 就是完全退出：托盘或菜单栏的「退出 Solaris」、macOS 的 ⌘Q 和 Dock 的「退出」、关闭动作为「退出 Solaris」时关闭主窗口，都先像「断开」一样断开本用户的隧道，再退出界面进程；服务记为未运行，睡眠唤醒（D34）和服务重启后都不再恢复。断开最多等 10 秒，失败或超时时询问重试还是仍然退出，并说明隧道可能仍在运行。关闭动作为「留在托盘 / 菜单栏」时关闭窗口只隐藏窗口。别的用户的隧道（D5b）、没有服务应答时不断开；安装器的 `--quit` 和系统注销、关机时直接退出，隧道交给服务（D5、升级保留 `was_running`）；界面崩溃或被结束时隧道仍由服务或扩展维持。取代"退出界面后隧道继续运行"：用户把退出理解为退出整个应用，退出后流量仍经隧道却没有界面，不易察觉 | [ui.md](ui.md) §8、[architecture.md](architecture.md) §1 |
| D42 | Windows 发布用 Azure Artifact Signing（原 Trusted Signing）签名，取代 D29。发布工作流经 GitHub OIDC 以 `release` 环境专用的托管标识登录 Azure（不选订阅），用 signtool 和 Artifact Signing 的 dlib 给安装目录里没有发布者签名的 exe、dll、pyd，以及安装界面插件、卸载器和安装器签名并加时间戳，任一步失败整个任务失败；dlib 只有 x86、x64 版，ARM64 runner 在 x64 仿真下运行它们。证书有效期只有几天、每天轮换，签名者只按证书主题里经验证的组织名 `Herta Network LLC` 和这个证书配置文件专属的 EKU 识别，不认具体证书或中间 CA：发布构建的服务（`SignedRelease`）这样校验界面，更新器这样校验下载的安装包 | [release.md](release.md) §1、§3、[architecture.md](architecture.md) §5.3、§10 |
| D43 | 1.0.0 之后的版本，GitHub Release 不再上传源代码包 `Solaris-<版本>-source.tar.gz`，只有三个安装包和 `SHA256SUMS`。对应的源代码在哪里，由发布说明的「源代码与许可」和 NOTICES 的开头写明：Solaris 是本仓库的 `v<版本>` 标签；mihomo 是 `github.com/Ember-Moth/mihomo` 上由这个标签的 `src/core/go.mod` 里 `replace` 固定的提交；其他 Go 模块（包括 Ember-Moth 的 cronet-go、sing-snell）的版本和校验和由 `go.mod`、`go.sum` 固定，从 Go 模块代理（默认 `proxy.golang.org`）取得；GitHub 给每个 Release 自动附带的"Source code"只有本仓库的代码。理由：项目公开，clone 仓库就能拿到源代码；源代码包约 492 MB，大头是 `go mod vendor` 带进来的 cronet-go 各平台预编译库，每个版本都要再传一份。GPL 方面：做法对应 GPL-3.0 第 6 条 d 项，不随二进制附带源代码，而是在二进制旁边写明对应源代码在哪个网络服务器上，并保证在需要的期限内一直可以取得。取舍是源代码不再是一个自足的文件，而是分在三处：本仓库的标签、mihomo 分支的固定提交和 Go 模块代理，从 v1.0.0 起这三处都一直可以取得。前两处由本项目保管，`v*` 标签和 mihomo 分支上被引用过的提交不删除；Go 模块代理是第三方服务，不归本项目控制，而第 6 条 d 项写明源代码不论放在谁的服务器上，保证可以取得的仍是发布者，所以代理上取不到某个模块时要由本项目另行提供。已发布的 1.0.0 Release 和它的源代码包保留不动：1.0.0 的 NOTICES 指向那个文件 | [release.md](release.md) §1、§5 |
| D44 | 1.0.0 之后，mihomo 增强版改在上游 `Alpha` 分支之上维护：Ember-Moth/mihomo 的 `Alpha` 分支是上游 `Alpha` 加上本项目的提交（Naive、AnyTLS + REALITY、REALITY 握手对齐、VLESS 反向代理、运行状态与 GEO 读取的同步修复），Solaris 的 replace 固定到其中的提交。Snell 改用上游的原生实现（含 v6），去掉 sing-snell，配置参数与上游一致：v6 模式是 `mode`，没有 `user-key`，v5 按 v4 连接，v6 不支持 http/tls 混淆。改用前确认没有用户在用 Snell，所以不做旧参数的兼容。代价是内核包含上游尚未正式发布的改动，升级时随上游 `Alpha` 变基。1.0.0 引用的 `Meta` 分支提交保留不动（D43） | [mihomo-fork.md](mihomo-fork.md)、[architecture.md](architecture.md) |
| D45 | 一键导入另外注册 `mihomo://install-config`（Clash Party）和 `clashmeta://install-config`（Clash Meta for Android、FlClash、Remnawave 的订阅页），格式、解析和处理与 `clash://` 相同：macOS 写进 `Info.plist`，Windows 安装器写在 HKLM，卸载时只删仍指向本程序的键。深链仍只预填导入表单，由用户按「导入」，不自动导入（任何网页都能打开这种链接）。Windows 上用户自己 HKCU 的注册优先于 HKLM：界面启动时检查四个 scheme 在 HKCU 的打开命令，只在它指向的程序不存在（而所在的盘存在）时删除这个用户级的键，让 Solaris 在 HKLM 的注册生效，并记日志写明 scheme 和原来的命令；指向仍存在的程序时不动，判断不了的命令也不动。不采纳「最后安装的客户端接管」：不覆盖用户仍然有效的选择，网页要指定 Solaris 时用 `solaris://` | [release.md](release.md) §6、§7 |
