# 开发方案

本文规划从现在到首个可发布版本的工作：现在做到了哪里、接下来按什么顺序做、每个里程碑怎样算完成、哪些决定要在
什么时候做出。各部分的设计见 [文档一览](#7-文档一览)。每完成一轮更新本文。

最近一次整理：2026-10-30，第四轮合并后。

## 1. 当前进度

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| 架构与视觉规范 | 完成 | architecture.md、ui.md、DESIGN.md；原型在 Claude Design 画布上（第 30 版，按页面分页，与 DESIGN.md 同步，过时的画板在"归档"页） |
| 控制协议 | v1 | 消息、方法表、帧编解码；Go 与 Python 共用样本测试；配置来源是订阅原文或拼装（`Composition`）；分流的 `InspectSource`、`ListGeoCategories`、`PreviewGroup`、`MatchRule`、`ConvertToPlan`、`ExportPlan`；`RegisterWarp`、`ListVpnGate`；组网的 `GetMeshStatus`、`LoginMesh`；链式出口与组网的字段；`RunOptions.tun_stack` |
| engine（内嵌 mihomo） | 完成 | mihomo v1.19.33；无 TUN、自管 TUN 和 fd 三种模式；覆写、§3.3 安全项、开发模式护栏；TUN 栈可选（D3），fd 模式下 `includeAllNetworks` 时换用 gVisor（D19） |
| Windows 服务 | 代码完成，待实测 | 全部内核方法与 `PollEvents`；认领与属主、注销停止与登录恢复、系统代理、客户端校验。逻辑用替身平台测，ACL、WinINet 和客户端校验在 CI 的 Windows 机器上测；服务本身还没在 Windows 虚拟机上跑过 |
| Python 宿主层 | 完成 | 控制客户端、全部请求映射、日志与连接接口、模拟宿主、macOS 的 `MacExtensionHost`；`core_available`（D27）与接管方式（D26） |
| 界面 | 完成 | 七页；覆写编辑；中英双语；浅色与深色；托盘、单实例、登录时打开、`--quit`；qmllint 与无障碍检查；应用图标"日冕"；自绘顶部区域；单个连接开关（D26）；没有内核时先存下订阅、隐藏需要内核的部分（D27）。Windows 的顶部区域只在 CI 上测过 |
| 端到端联调 | 完成 | `tests/e2e` 在 CI 的 macOS 上让界面连真实开发服务，走通导入、连接、分流和方案的流程；节点是本机起的代理，没用真实订阅 |
| 数据存储与备份 | 本地与 iCloud 完成 | SQLite，墓碑保留 90 天；快照的导出与恢复（替换或合并）；本地目录和 iCloud（CloudKit）提供方；自动备份接到界面上。iCloud 还没在真机上运行过；Google Drive 未做 |
| 自定义分流 | 完成（M8a、M8b） | 分流方式、节点池、地区组、默认模板、41 个服务、规则编辑与撤销、立即生效；策略组编辑器与预览、规则测试、搜索与按目标筛选、以此创建方案、导入导出、查看生成的配置 |
| 链式出口与组网 | 除 Tor 外完成 | WARP 出口（M9）、VPN Gate 出口（M12）、组网出站单独处理（D24）、应用内组网（M11）的内核与界面都已完成，只对假服务器、构造的出站和模拟宿主验证过，真实验收未做。Tor（M10）未做 |
| macOS 原生部分 | 代码完成，待实测 | Xcode 工程、管理库 C 接口；系统扩展用 utun 以 fd 模式运行内核，带 GEO 数据；"包含所有网络"（D19）；测试 Mac 实测未做 |
| 打包与发布 | 已写好，未实测 | macOS：`scripts/build_app.py` 组装并签名 `Solaris.app`。Windows：`scripts/build_windows.py` 和 NSIS 安装器，首发不签名（D29），CI 构建安装包并在 runner 上安装、升级、卸载。`release.yml` 签名、公证、出 .dmg 和安装包，建草稿 Release，附 `SHA256SUMS` 和源代码包；还没运行过 |
| 发布准备（M6） | 只差第一次发布 | 检查更新与下载（Windows 不签名时只核对 SHA-256）、诊断信息导出、完整的第三方许可证清单、一键导入链接、README 的下载与安装、`PRIVACY.md`、发布说明模板 |
| CI | 全绿 | Python（macOS、Windows）、Go（macOS、Windows、Linux）、macOS 上的 e2e、Apple 的不签名构建、Windows 安装包的装卸、网络命名空间里的 TUN 测试（四种栈和 fd 模式） |

## 2. 开发计划

按轮推进。同一轮里的几项改动的文件互不重叠，各开一个会话同时做；每个会话先开 PR 跑 CI，再由主会话合并。

### 已完成

| 阶段 | 完成 | 内容 |
| --- | --- | --- |
| A | 2026-10-29 | 修好 CI；界面连真实开发服务的端到端联调（`tests/e2e`）；M3 收尾（qmllint、无障碍、焦点） |
| B | 2026-10-29 | SQLite 存储层（M7 的 B1）；分流的内核部分（M8a 的 B2：`Composition`、拼装、GEO 数据） |
| 改名 | 2026-10-30 | 产品名定为 Solaris（D1），代码里的标识全部直接替换 |
| C1 | 2026-10-30 | 分流的界面部分（M8a） |
| D 第一轮 | 2026-10-30 | M8b 分流进阶；M5 收尾（`MacExtensionHost`、组装 `Solaris.app`、发布工作流）；M6 先行项 |
| D 第二轮 | 2026-10-30 | M9 WARP 出口（内核、界面）；组网出站单独处理（D24）；M7 备份的本地部分 |
| D 第三轮 A | 2026-10-30 | M4 服务（属主、恢复、系统代理、客户端校验）；M4 打包（Nuitka、NSIS、CI 装卸、发布 job）；"TUN 协议栈"与"包含所有网络"；应用图标 |
| 第四轮 | 2026-10-30 | 自绘顶部区域；单个连接开关（D26）；没有内核时的导入（D27）；发布与不签名的 Windows 版（D29）；`--quit`、墓碑清理、表结构复核；M12 VPN Gate 出口；M11 应用内组网；M7 的 iCloud 部分。合并前两份审查找出的问题（组网的监听与启动时机、iCloud 失败重试、断开时换订阅、VPN Gate 列表的输入校验）都已修好 |

### 已知问题

- **不稳定的测试**：TUN 测试在 gVisor、mixed 栈上偶尔报"mihomo carried no traffic"；Windows runner 保留了一段端口时，
  Go 测试绑定 UDP 端口失败；`tests/test_backup_viewmodel.py` 的验收在 Windows 上偶尔读到空的订阅行。都与改动无关，重跑
  能过，要逐个修稳。
- **race 检测**：`go test -race ./internal/engine/` 在 main 上就有数据竞争报告（mihomo 的全局状态和测试之间的 GEO 更新
  协程），CI 不跑 `-race`。
- **订阅页的节点数**含组网出站，mesh.md 第 3 节写的是不计入，要改一边。

### 第三轮 B：实测（等环境）

- **M4 实测**：wintun、strict-route、DNS 泄漏、四种栈、安装升级卸载；服务的认领与换属主重启、注销停止与登录恢复、
  系统代理的模拟写入；Windows 的顶部区域。需要 Windows 虚拟机。
- **M5 实测**：系统扩展审批、utun、四种栈、D8、`includeAllNetworks`、第一次公证；断开时导入、连接后读取（D27）；iCloud
  备份。需要测试 Mac。
- **链式出口与组网的真实验收**：M9 WARP、M12 VPN Gate（exits.md 第 10 节）和 M11 组网（mesh.md 10.8 节），在开发服务上做
  （不建 TUN）。需要真实节点和你自己的组网账户。
- **M6 首发**：第一次运行发布工作流，发布草稿 Release，"检查更新"对真实 Release 验证。

### 首发清单

首发要求 Windows x64 和 macOS arm64 同时可用（D2）。下面每一项都完成才发布：

| 项 | 状态 |
| --- | --- |
| Windows 服务、安装器、实测（M4） | 代码完成，安装器在 CI 上装卸验证过；实测等虚拟机 |
| macOS 在全新 Mac 上的验收（M5），第一次公证 | 代码完成；等测试 Mac |
| "TUN 协议栈"与"包含所有网络"设置 | 代码完成；四种栈和 `includeAllNetworks` 的实测、各平台的默认栈，等虚拟机和测试 Mac |
| 自绘顶部区域（DESIGN.md「顶部区域」） | 完成；Windows 的窗口按钮、拖动和贴靠等虚拟机验收 |
| 单个连接开关（D26） | 完成 |
| 应用图标与品牌素材 | 完成："日冕"，`scripts/gen_icons.py` 生成全部尺寸，已接入 `Solaris.app`、`Solaris.exe` 和安装器 |
| Windows 代码签名 | 首发不签名（D29）；签名方案定下后再接上签名者常量和 `SignedRelease` |
| 发布工作流：两个平台的安装包、发布到 GitHub Releases、SHA256SUMS；"检查更新"对真实 Release 验证 | 已写好：两个平台的安装包、草稿 Release、`SHA256SUMS`、源代码包；还没运行过 |
| WARP、VPN Gate 出口和组网的真实验收（M9、M12、M11） | 等真实节点和组网账户 |
| 真实网络测试：真实订阅、DNS 泄漏、IPv6、睡眠唤醒、切换网络 | 两个平台实测时一起做 |
| 许可证清单补全 | 完成 |
| 用户可见的材料：下载页或 README、发布说明、隐私说明 | 完成：README 的下载与安装、`PRIVACY.md`、`.github/release-notes.md` |
| 数据库表结构定稳：首发后不再"不写兼容代码"，改表要写迁移 | 第四轮已复核；首发前不再改表 |

**建议放到首发之后**（待你确认）：Google Drive 备份、M10 Tor。

### 首发之后

- **Google Drive 备份**（C2，等 Google Cloud 项目）。
- **M10 Tor 出口**（实验功能）：arti 经 pyo3 运行在界面进程，见 [exits.md](exits.md) 第 4 节。

### 需要你提供或决定的

| 事项 | 最晚何时 | 用途 |
| --- | --- | --- |
| 单独的测试 Mac 或 macOS 虚拟机 | 现在 | 系统扩展实测、D8、`includeAllNetworks`、断开时导入、iCloud |
| Windows 测试机或虚拟机 | 现在 | M4 实测、Windows 的顶部区域验收 |
| 一个只有 TCP 的真实节点 | 链式出口验收 | 验证 WARP 出口的 MASQUE over HTTP/2、VPN Gate 出口 |
| 你自己的 Tailscale、ZeroTier 或 EasyTier 网络 | M11 验收 | 应用内组网的登录、状态和规则 |
| Windows 代码签名方案 | 首发之后（D29） | Authenticode 签名、服务对客户端的签名校验 |
| Google Cloud 项目与"桌面应用"类型的 OAuth 客户端 | Google Drive 备份开工前（同意屏幕审核要时间） | Google Drive 备份 |
| D5b、D8、D28 | 见第 5 节 | — |

Apple 的签名材料（证书、两份描述文件、公证 API Key）已齐，见 apple/README.md。

## 3. 里程碑

### 已验收

| 里程碑 | 验收 | 还没覆盖的 |
| --- | --- | --- |
| M1 内核跑通（开发模式，不建 TUN） | 开发服务用真实配置启动内核，`curl -x` 走代理，本机网络不变；界面各页显示真实数据；CI 的网络命名空间 TUN 测试通过 | 节点是本机起的代理，没用真实订阅 |
| M2 订阅管理 | 导入订阅 → 校验 → 使用 → 连接 → 订阅更新后原地重载 | — |
| M3 其余页面与日常功能 | 七页在浅色和深色下与原型、DESIGN.md 一致；只用键盘能完成全部操作；关闭窗口后可从托盘恢复 | Windows 上的界面验收、真实读屏软件 |
| M8a 自定义分流：基础 | 只有节点的订阅自动用默认方案连接；"Telegram → 日本"生效；切到"订阅自带"后按订阅的规则走（e2e） | 真实订阅 |
| M8b 自定义分流：进阶 | 400 条规则的订阅转成方案、摘要准确；策略组预览与连接后的成员一致；规则测试与实际命中一致（`tests/e2e/test_plans_ui.py`） | 规则测试只经混合端口验证过，TUN 和 fake-ip 下的一致性留到 M4、M5 实测（routing.md 第 12 节） |

### M4 Windows 服务产品化

目标：在全新的 Windows 上完成安装、连接、开机恢复、升级和卸载的完整流程。设计见 architecture.md 第 5、9 节。

**状态**：代码完成（2026-10-30，第三轮 A）。
- 服务：认领与属主（`internal/owner`，一个服务进程只服务一个属主，换属主时经辅助进程重启；home 是 `users\<SID>`，只有
  SYSTEM 能写、属主能读）；`profile.json`、`state.json` 和 `cache.db` 里的节点选择用于恢复，只在属主登录时恢复，注销时
  停止（D5）；系统代理模拟属主会话写 WinINet 设置并在停止时写回；客户端校验，签名的发布构建（`SignedRelease`）校验
  签名者常量。界面在要做事时认领，其他用户占用时概览页提示。
- 打包：`scripts/build_windows.py` 构建服务和界面并编译 `package/windows/installer.nsi`（中英两种语言）；安装器注册并
  启动服务，注册 `solaris:` 和 `clash:`；升级时停服务、按清单删除旧文件；卸载时由用户选择是否删除用户数据。CI 的
  `windows-installer` job 在 runner 上安装、打开界面、升级、卸载、再以 `/PURGE` 卸载；`release.yml` 的 Windows job 产出
  安装包和 `.sha256`，签名步骤留空。

**剩下**：
- 虚拟机实测：服务以 SYSTEM 运行时的认领与重启辅助进程、`SessionLogon` / `SessionLogoff`、模拟 token 写 HKCU 后浏览器
  生效、开机后登录时恢复、真实 ProgramData 下的 ACL、Nuitka 构建的界面通过客户端校验；wintun、strict-route、DNS 泄漏、
  四种栈；UAC 提示、升级后恢复隧道。
- 没做的：签名（首发不签名，D29）；管理员接管（D5b）；PowerEvent 唤醒后重置连接、超时逐层递减。安装器升级、卸载时
  先用 `--quit` 请界面退出，10 秒没退出才结束进程。

**验收**：在全新的 Windows 虚拟机上依次完成以下步骤，并且 DNS 泄漏测试通过：
1. 安装后连接；
2. 关闭界面，隧道仍在运行；
3. 重启系统，隧道自动恢复；
4. 升级；
5. 卸载后不留服务和运行文件。

### M5 macOS NetworkExtension

目标：发布形态的 .app 在全新的 Mac 上通过系统扩展提供隧道。设计见 architecture.md 第 6 节。

**状态**：代码完成（2026-10-30）。XcodeGen 工程（`apple/project.yml`）、`SolarisManager.dylib` 的 C 接口、PacketTunnel
系统扩展；扩展找到 utun 描述符后交给 engine 以 fd 模式运行（`with_gvisor`），attach 时把 GEO 数据复制进 home；
`MacExtensionHost` 经 ctypes 调用管理库；"包含所有网络"（D19）由管理库写进 VPN 配置，扩展在 attach 时告诉 engine；
`scripts/build_app.py` 组装并签名 `Solaris.app`（本机签名校验通过）；`release.yml` 签名、公证、staple、出 .dmg 和
`.sha256`。CI 做不签名的构建，并在网络命名空间里测 fd 模式。

**剩下**：测试 Mac 实测（找 utun 描述符、四种栈、重连时描述符的归属、`includeAllNetworks`、D8、hardened runtime 下的
启动、登录项 `SMAppService`）；第一次运行发布工作流（公证）；D28。部署目标暂定 macOS 15.0。

**验收**：在全新的 Mac 上安装公证后的 .app → 批准系统扩展 → 连接 → 退出 App 后隧道仍在 → 重新打开 App 能读到状态。

### M6 发布准备

设计见 [release.md](release.md)。

**状态**：先行项完成（2026-10-30，第一轮）：检查更新与下载（D11；GitHub Releases、SHA256SUMS、macOS 的 codesign /
spctl 与 Team ID、Windows 的 WinVerifyTrust，确认后才打开安装包）、诊断信息导出（按白名单脱敏）、
`scripts/gen_licenses.py` 生成的第三方许可证清单、`solaris://` 与 `clash://` 一键导入链接（D6）。macOS 的 scheme 由
`Solaris.app` 的 Info.plist 注册，Windows 的由安装器注册。

第四轮补上了：`release.yml` 建草稿 Release，附两个安装包、`SHA256SUMS` 和源代码包；Windows 安装包不签名时检查更新只核对
SHA-256，确认框写明未签名（D29）；完整的许可证清单；README 的下载与安装、`PRIVACY.md`、发布说明模板。

**剩下**：第一次运行发布工作流，之后让"检查更新"对真实 Release 验证（现在只对本机的假服务器测过）；崩溃日志。

### M7 数据存储与云备份

目标：用户数据存进 SQLite；换设备后登录 Google 或 iCloud，就能恢复订阅、覆写、分流方案和设置。设计见
[storage.md](storage.md)。

**状态**：存储层（B1）和本地部分完成（2026-10-30，第二轮）：快照的导出、读取与恢复（替换或按记录合并，删除的记录以
墓碑参与合并）、`BackupProvider` 接口与内存、本地目录提供方、自动备份的逻辑（去抖、摘要比较、24 小时补一次、保留
10 份，用内存提供方测试），设置页"备份与同步"的导出到文件和从文件恢复。`tests/test_backup_viewmodel.py` 用模拟宿主和
本机的假订阅服务器跑通了不经云端的验收：导出后在空数据目录恢复，全部还原，订阅重新下载并校验；两个数据目录各改一部分
后合并，较新的修改胜出。

iCloud 部分完成（2026-10-30，第四轮）：管理库里的 CloudKit 函数（私有数据库，容器 `iCloud.dev.tschen.solaris`）、
Python 的 iCloud 提供方、设置页的 iCloud 一行；自动备份随它接到界面上，失败后不再频繁重试，留给每小时一次的补备份。
CloudKit 还没真正运行过：开发机上不运行签名的 .app，测试 Mac 上的手动步骤见 apple/README.md。

**剩下**：iCloud 在测试 Mac 上的验收；Google Drive（C2：OAuth 登录、appDataFolder，等 Google Cloud 项目）。

**验收**（云端部分）：
1. 在一台机器上导入几份订阅、覆写和分流方案，登录 Google 后自动备份；
2. 在另一台机器上（或清空数据目录后）登录同一账户并恢复，全部还原，订阅重新下载并通过校验；
3. macOS 上用 iCloud 走一遍同样的流程。

### M9 WARP 出口

目标：流量经用户的节点再进入 Cloudflare WARP，节点看不到访问的网站，网站看不到节点 IP。设计见
[exits.md](exits.md) 第 3 节（D22）。

**状态**：内核和界面完成（2026-10-30，第二轮）。
- 内核：`internal/warp` 经"经由"组注册与换密钥、`RegisterWarp`、拼装 MASQUE / WireGuard 出站（`dialer-proxy` 指向经由组）、
  `egress_unavailable` 和 `via_empty` 警告，只对本机的假服务器测过。
- 界面：链式出口一节、WARP 出口设置对话框、规则目标与"未启用"、规则行的警告提示、账户经 `keyring` 存系统凭据库、本机
  设置 `warp_protocol`、方案的 `plans.egress`，只用模拟宿主验证过（`tests/test_routing_exits.py`）。

**验收**（需要真实节点，在开发服务上做，不建 TUN）：
1. 经一个只有 TCP 的真实节点打开 WARP 出口，注册成功；
2. "ifconfig.me → WARP 出口"返回 Cloudflare 的 IP，而不是节点 IP；
3. 删除账户后，同一规则的连接被拒绝；
4. 节点支持 UDP 时，换成 WireGuard 结果相同。

### M10 Tor 出口（首发后，实验功能）

目标：流量经用户的节点进入 Tor 网络，不需要网桥，可以访问 `.onion`。设计见 [exits.md](exits.md) 第 4 节（D23）。

范围：

- `src/tor`：arti-client 的 pyo3 模块，上游连接只经内核的 `tor-upstream` 入站，自带 SOCKS5 服务，按目标主机隔离线路；
- 构建：maturin 进 Python 的 CI 和两个平台的打包；
- 界面：设置页"实验功能"、生命周期、出口行的连接进度；
- 内核：拼装 `tor-upstream` 入站和"Tor 出口"出站；服务清单加"Onion 网站"。

验收：一分钟内连上 Tor 网络；check.torproject.org 显示正在使用 Tor；能打开 `.onion` 网站；退出界面后同一规则的
连接被拒绝；抓包确认 arti 没有直连流量。

### M11 应用内组网

在应用里添加 Tailscale、ZeroTier、EasyTier 网络：凭据存系统凭据库，Tailscale 交互式登录，显示组网状态。设计见
[mesh.md](mesh.md) 第 10 节。

**状态**：内核、协议（`GetMeshStatus` 57、`LoginMesh` 58）和界面完成（2026-10-30，第四轮），只对构造出来的出站、假数据和
模拟宿主测试过。订阅带的组网出站仍然有流量经过时才启动，EasyTier 的监听一律关闭（mesh.md 10.6 节）。

**验收**：用你自己的网络，经开发服务做，步骤见 mesh.md 10.8 节。

### M12 VPN Gate 出口（D25）

经节点拉取 VPN Gate 的服务器列表，只用 TCP 的 OpenVPN 服务器；默认负载均衡组按网站分配服务器并跳过失效的。设计见
[exits.md](exits.md) 第 5 节。

**状态**：内核、协议（`ListVpnGate` 56）和界面完成（2026-10-30，第四轮），只对假列表服务器和模拟宿主测试过。使用条款在
开工前核实（exits.md 5.1 节）：官网允许用 CSV 列表做自己的客户端；标着"Academic Use Only"的服务器一律不用。列表按
不可信的输入处理：每份配置限大小，只接受公网地址。

**验收**：经真实节点在开发服务上做，步骤见 exits.md 第 10 节。

## 4. 协作方式

- **本机开发不改动本机网络**：开发机上不创建 TUN，不设置系统代理，不修改路由和 DNS，也不在本机真实注册登录项或计划任务。界面用模拟宿主或无 TUN 的开发服务调试；需要 TUN 的测试放到 CI 的网络命名空间或测试虚拟机里做。
- **先设计，再开工**：大的功能先写设计文档（如 storage.md、routing.md），原型跟着设计修改，定稿后再拆成会话实现。
- **并行会话按文件划分**：每个会话写明可以改哪些文件，避免两个会话改同一处；协议、翻译目录、`Main.qml`、`composition.py` 这类共享文件，由主会话在合并时统一处理。
- **协议变更**：先改 `control.proto`，运行 `scripts/gen_protocol.py`；在同一个提交里同步修改 Go 分发器、Python `ServiceHost` 和模拟宿主，并用共享样本守住两端一致。
- **视觉变更**：先改 DESIGN.md，再运行 `scripts/gen_theme.py`；原型画布同步修改。
- **文案变更**：中文是源语言；改动文案后运行 `scripts/gen_i18n.py`，并在 `en.ts` 里补齐英文。文案保持中立、简短（DESIGN.md 的 Typography 一节）。
- **mihomo 升级**：钉在 `Meta` 分支的 tag 上；升级前跑完整测试，并按 architecture.md §3.3 逐条复核安全要求。

## 5. 决策

### 5.1 待决策

按最晚需要决定的时间排列，越早决定，返工越少。

| # | 事项 | 最晚何时 | 影响 |
| --- | --- | --- | --- |
| D5b | 内核运行中能否由管理员接管其他用户的隧道（倾向可以，需 UAC 确认） | M4 实测前 | 服务的属主逻辑；接管加在 `owner.Service.Claim` 里，现在只能等隧道停了再认领 |
| D8 | provider message 单条大小上限 | M5 实测 | `Reload` 能否直接携带大配置 |
| D28 | 支持的最低 macOS：PySide6 6.12.0 的绑定库要求 macOS 26，要支持 15 得钉在 6.11.x（apple/README.md） | 首发前 | 部署目标、依赖版本 |

### 5.2 已决定（2026-10-29 至 10-30）

| # | 决定 | 详见 |
| --- | --- | --- |
| D1 | 产品名与应用名都是 Solaris，定稿不再改变。GitHub 仓库为 `Ember-Moth/Solaris`，macOS bundle id 前缀 `dev.tschen.solaris` | architecture.md |
| D2 | 构建架构：Windows 只做 x64，macOS 先只做 arm64 | 本文 M4、M5 |
| D3 | Windows 带 `with_gvisor` 构建，四种 TUN 栈都提供，由用户在设置里选择；macOS 系统扩展同样带 `with_gvisor`，fd 模式默认 gVisor 栈 | architecture.md §3.4 |
| D4 | 覆写只做 Merge，不做 Script；一份全局覆写，加上每份订阅各一份 | ui.md §4 |
| D5 | 属主注销时停止隧道，下次登录后恢复 | architecture.md §5.4 |
| D6 | 订阅一键导入注册 `solaris://install-config?url=…` 和 `clash://install-config` | release.md |
| D7 | macOS 的配置和 Windows 一样，经控制协议的 `Start` / `Reload` 传入 | architecture.md §6.2 |
| D9 | 深色主题放在 M3，先补 DESIGN.md 再实现 | ui.md §6 |
| D10 | 许可证：GPL-3.0-only | LICENSE |
| D11 | 内置检查更新与下载：安装包来自 GitHub Releases，校验签名，由用户确认安装 | release.md |
| D12 | 界面提供简体中文和英文，中文为源语言 | ui.md §5 |
| D13 | 本地用 SQLite；云端只做备份与恢复，不做双向同步；快照不加密；Google Drive 在应用内登录，用 appDataFolder；iCloud 用 CloudKit，仅 macOS | storage.md |
| D14 | Google 令牌存放在系统凭据库（macOS 钥匙串、Windows 凭据管理器），用 `keyring` | storage.md 第 4 节 |
| D15 | 自定义分流：<br>• 全局开关"订阅自带 / 我的方案"，首次导入时自动选择；<br>• "我的方案"下所有订阅组成节点池，可单独排除；<br>• 可以有多套方案；<br>• 规则条目按意图记录，默认按服务添加；<br>• 规则类型为 GEOSITE / GEOIP、域名、IP 段 | routing.md |
| D16 | 分流配置在内核里拼装，节点筛选和规则匹配都用 mihomo 自己的逻辑 | routing.md 第 8 节 |
| D17 | GEO 分类列表由内核从正在使用的 GEO 数据里读取；安装包附带初始 GEO 数据，另附一份分类名清单作兜底 | routing.md 第 8 节 |
| D18 | GEO 数据全量随安装包附带（geosite 4.4 MB、geoip 16.5 MB） | routing.md 第 8 节 |
| D19 | macOS 扩展的 `includeAllNetworks` 默认关闭，用户可在设置页打开；打开后 `system`、`mixed` 栈换成 gVisor | architecture.md §3.4、ui.md §8 |
| D20 | fd 模式的 `dns.fake-ip-range` 固定为 `198.18.0.1/16`（TUN 地址由它得出）；用户关掉 `ipv6` 时，进入隧道的 IPv6 流量被丢弃 | architecture.md §3.1 |
| D21 | 链式出口只在"我的方案"下提供；出口不可用时，指向它的规则拒绝连接，不改走普通代理 | exits.md 第 1 节 |
| D22 | WARP 出口放在内核：mihomo 的 MASQUE 或 WireGuard 出站，`dialer-proxy` 经用户的节点，默认 MASQUE over HTTP/2；内核经节点注册账户，账户存系统凭据库、不进备份；首发前做（M9） | exits.md 第 3 节 |
| D23 | Tor 出口用 arti-client，经 pyo3 运行在界面进程；首发后作为实验功能（M10）；退出界面后 Tor 不可用，连接被拒绝 | exits.md 第 4 节 |
| D24 | 保留 Tailscale、ZeroTier、EasyTier 出站；它们不进节点池和任何策略组，作为"组网"出站单独列出、供规则引用；只用作出站，安全项关闭 EasyTier 对外提供出口和子网的选项 | mesh.md |
| D25 | VPN Gate 作为第三种链式出口：经节点拉列表，只用 TCP 的 OpenVPN 服务器，按国家和评分挑选；默认负载均衡组按网站分配服务器并跳过失效的，也可选只用一台；定位是换 IP 和住宅 IP，不宣传隐私；开工前先核实使用条款 | exits.md 第 5 节 |
| D26 | 界面上只有一个连接开关（概览页出站分组第一行）；连接时怎样接管流量由设置页「接管方式」决定，TUN 和系统代理默认都开；macOS 上 TUN 是必选的 | DESIGN.md「连接开关」、ui.md 第 8 节 |
| D27 | 订阅由 Python 下载、Go 内核解析和校验，只此一份解析。没有内核能应答时（macOS 隧道断开）先存下原文，连接后、`Start` 之前再解析和校验；界面在没有内核时隐藏需要它的部分 | ui.md 第 3 节 |
| D29 | 首发的 Windows 安装包不签名：服务对客户端只做文件身份校验，检查更新只核对 SHA-256；签名方案定下后再接上 | release.md |

## 6. 风险

| 风险 | 应对 |
| --- | --- |
| CI 变红后新问题被已有的失败掩盖 | 并行会话合并前先在 PR 上跑 CI；main 变红时先修 |
| 模拟宿主与真实服务的行为再次出现出入 | e2e 测试核对两者对配置的判定；新增宿主方法时同时补 e2e 用例 |
| mihomo 以全局状态运行，反复启停可能泄漏资源 | 启停压力测试；Stop 后核对 goroutine 和文件描述符 |
| mihomo 内部接口（`hub`、`executor`、`tunnel`）随版本变化 | 钉住 tag；engine 只通过少量入口调用 mihomo，升级时集中修改 |
| Windows 上 wintun、strict-route 的行为差异和 DNS 泄漏 | M4 专项实测，形成检查清单 |
| 开发机不跑 TUN，TUN 相关问题发现得晚 | CI 的网络命名空间测试；尽早准备测试虚拟机和测试 Mac |
| 服务、系统扩展和安装器只在 CI 和替身里测过 | 第三轮 B 的实测；拿到环境前不再加依赖真机的功能 |
| 系统扩展的审批、签名和公证流程复杂 | 先用最小可用的扩展跑通一次公证和安装，再补全功能 |
| 只在 macOS 上看过界面渲染 | M4 实测时在 Windows 上做界面验收 |
| 云端快照不加密，订阅链接会随云端账户一起泄露 | 开启备份时明确提示；只申请 appDataFolder 权限；快照不含令牌 |
| Google OAuth 同意屏幕审核耗时 | 尽早建 Google Cloud 项目并提交审核 |
| CloudKit 依赖签名、entitlement 和 M5 的管理库 | iCloud 放在 M7 的最后，与 M5 一起验证 |
| WARP 的注册接口不是官方的，Cloudflare 可能改动或限流 | 常量集中在 `internal/warp`；失败时拒绝连接，不退回普通代理；功能默认关闭 |
| arti-client 每月发一个不兼容版本；Rust 工具链进入构建 | Tor 放到首发后，作为实验功能；钉住版本，每季度升级 |
| VPN Gate 的使用条款未核实；出口由志愿者运营 | 开工前核实，不允许就不做；只宣传换 IP，不说隐私 |

## 7. 文档一览

| 文档 | 内容 |
| --- | --- |
| [architecture.md](architecture.md) | 系统架构：engine、控制协议、Windows 服务、macOS 扩展、安全模型、构建与测试 |
| [ui.md](ui.md) | Python 界面：宿主接口、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为、分流页 |
| [storage.md](storage.md) | SQLite 存储与备份（M7） |
| [routing.md](routing.md) | 自定义分流（M8a、M8b） |
| [exits.md](exits.md) | 链式出口：WARP（M9）、Tor（M10）和 VPN Gate（M12） |
| [mesh.md](mesh.md) | 组网出站：Tailscale、ZeroTier、EasyTier 的单独处理（D24）和应用内组网（M11） |
| [release.md](release.md) | 发布物约定、检查更新与下载、诊断信息、第三方许可证、一键导入（M6） |
| [DESIGN.md](../DESIGN.md) | 视觉规范与文案规则，Theme.qml 由它生成 |
| roadmap.md（本文） | 进度、计划、决策与风险 |
