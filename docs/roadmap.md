# 开发方案

本文规划从现在到首个可发布版本的工作：现在做到了哪里、接下来按什么顺序做、每个里程碑怎样算完成、哪些决定要在
什么时候做出。各部分的设计见 [文档一览](#8-文档一览)。每完成一个阶段更新本文。

最近一次整理：2026-10-30，定下阶段 D 的三轮计划后。

## 1. 当前进度

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| 架构与视觉规范 | 完成 | architecture.md、ui.md、DESIGN.md；原型在 Claude Design 画布上（第 21 版，含深色画板、分流 v2 与隐私出口） |
| 控制协议 | v1 | 消息、方法表、帧编解码已定；Go 与 Python 共用样本测试；配置来源可以是订阅原文或拼装（`Composition`）；`InspectSource`、`ListGeoCategories` |
| engine（内嵌 mihomo） | 完成（M1、M5） | mihomo v1.19.33；无 TUN、自管 TUN 和 fd 三种模式，覆写、§3.3 安全项、开发模式护栏 |
| Go 分发器与服务入口 | 部分 | 全部内核方法、Start / Stop / Reload / Validate、`PollEvents`；`Claim`、系统代理、Authenticode 校验留到 M4 |
| Python 宿主层 | 完成 | 控制客户端、全部请求映射、日志与连接接口、模拟宿主 |
| 界面 | 完成（M2、M3、C1） | 七页全部可用（新增分流页，"配置"改名"订阅"）；覆写编辑；中英双语；浅色与深色；托盘、单实例、登录时打开；qmllint 与无障碍检查进了测试 |
| 端到端联调 | 完成（A2） | 界面连真实开发服务跑通 M1、M2 的验收步骤；`tests/e2e` 自动化，CI 的 `e2e` job 在 macOS 上运行 |
| 数据存储与云备份 | 存储层完成（B1） | 界面数据在 SQLite；云备份未做 |
| 自定义分流 | M8a 完成（B2、C1） | 分流方式、节点池、地区组、默认模板、41 个服务、规则编辑与撤销、立即生效；M8b（策略组编辑器、规则测试、导入导出）未做 |
| 隐私出口 | 设计完成 | WARP 出口（M9）和 Tor 出口（M10，首发后）的设计见 privacy.md，未实现 |
| macOS 原生部分 | 部分（M5） | Xcode 工程、管理库 C 接口；系统扩展用 utun 以 fd 模式运行内核（`with_gvisor`）；Developer ID 签名与校验通过；`MacExtensionHost`、组装 .app、公证、测试 Mac 实测未做 |
| 打包与安装 | 未开始 | 只有 Nuitka 入口文件和资源复制规则 |
| CI | 全绿 | Python（macOS、Windows）、Go（macOS、Windows、Linux）、网络命名空间 TUN 测试（四种栈和 fd 模式）、macOS 上的 e2e |

## 2. 下一步开发计划

按阶段推进。同一阶段里标了"可并行"的几项改动的文件互不重叠，可以各开一个会话同时做，最后统一合并。

### 阶段 A：恢复 CI，跑通端到端（已完成，2026-10-29）

| 编号 | 工作 | 内容 | 可并行 |
| --- | --- | --- | --- |
| A1 | CI 修复 | engine 测试 `TestStartProxiesTrafficAndStopClosesThePort` 的端口冲突（随机端口被抢占）；日志里出现了 external-controller 的 unix 监听被启动又关闭，要确认它来自哪个测试，确认正式路径上 §3.3 第 2 条仍然成立；Windows 上 `test_second_instance_shows_the_first` 超时；让 GitHub 上的 TUN job 跑通 | 与 A3 并行 |
| A2 | 端到端联调 | 在 Mac 开发模式下，界面连真实开发服务：导入配置 → 校验 → 连接 → `curl -x` 走代理 → 概览、代理、连接、日志显示真实数据 → 切换模式和节点 → TUN 和系统代理开关提示"开发模式不支持"。实测只改规则时重载会不会断开已有连接（routing.md 第 12 节）。全程不建 TUN | A1 之后 |
| A3 | M3 收尾 | qmllint 加入 CI；无障碍检查（键盘遍历、读屏名称）；删除或保存后焦点回到原控件；把第 7 节列出的规则补进 DESIGN.md | 与 A1 并行 |

完成标准：CI 全绿；M1、M2、M3 的验收项全部通过；联调中发现的问题修完或记入本文。三项都已达到。

A1 查明的根因（2026-10-29）：

- engine 测试失败的真正原因是 CI 按 go.mod 装了 Go 1.26.0，测试二进制不记录依赖版本，"内核版本"断言失败；
  `address already in use` 来自故意占用端口的另一个测试。go.mod 加了 `toolchain go1.27.3`。
- 日志里的 unix 控制器来自 `TestSecurity3` 故意启动的控制器，正式的准入流水线没有问题；补了经 `Start` 的回归测试。
- Windows 上 Qt 的管道没有缓冲，第二个实例的唤起消息要等第一个读走才算写完，等待时间改为最多 5 秒。
- TUN job 里 sing-tun 会调用 runner 的 `resolvectl`，导致 gvisor 组挂起；测试关闭了 TUN 的 DNS 接管，CI 对比测试前后的 DNS。

A3 的结果：qmllint 在 pytest 里运行；配置、策略组、节点列表改为按行更新，焦点不再丢失；每个控件都有读屏名称和角色；
DESIGN.md 补了表格、标题栏、浮层、输入、托盘、键盘与读屏的规则。

A2 联调中发现并修好的问题（2026-10-29）：

- 混合端口改成被占用的端口时，Reload 先关了旧端口，内核继续运行却没有入站，信息里也没有原因。engine 现在应用
  前先试开新端口，失败时什么都不改，信息是系统的监听错误；界面显示「配置未能生效：…」。
- 连接的规则和规则列表用的是 mihomo 内部类型名（`DstPort`），改为配置写法（`DST-PORT`）。
- 没测过速的节点显示"超时"。协议的 `delay_ms` 改为：没测过不填，测速失败为 0；界面显示"—"和"超时"。
- 概览"最近连接"里，连向 IP 的连接主机名为空，速率隔一秒掉到 0（内核每秒一份快照，与轮询错开时会拿到同一份）。
  改为显示目的地址，速率与连接页一样按最近几次轮询平均。
- 服务断开又恢复后，概览一直显示"未能连接到服务"。
- 模拟宿主拒绝没有节点的配置，真实服务接受；模拟宿主改为像 mihomo 一样检查策略组和规则引用的节点，它的示例
  配置也改成真实内核能通过的。端到端测试核对两者对一组配置的判定一致。

### 阶段 B：存储层与分流内核（B1、B2 已完成，2026-10-29；B3 等 Windows 环境）

| 编号 | 工作 | 内容 | 可并行 |
| --- | --- | --- | --- |
| B1 | M7 存储层 | SQLite 数据库、schema 迁移；`ProfileStore` 和设置存储改为基于数据库；分流相关的表按 routing.md 第 9 节建好 | 与 B2 并行 |
| B2 | M8a 内核部分 | 协议新增配置来源 `Composition`、`InspectSource`、`ListGeoCategories`；engine 拼装配置（内联 provider、地区组、规则展开和对照表）；安装包附带的 GEO 数据；测试 | 与 B1 并行 |
| B3 | M4 Windows 服务产品化 | 见第 3 节。前提是有 Windows 测试机或虚拟机 | 与 B1、B2 并行 |

B1 只改 Python，B2 改 Go 和协议。协议变更时两边需要同步改 Python 的 `ServiceHost`；这部分由 B2 负责，B1 不碰 `host/`。

B1、B2 的结果与遗留：

- B1：所有线程共用一个连接，由锁串行；表结构按版本迁移；坏库改名保留后新建。B1 写的旧文件存储导入已删除（项目
  未发布，没有旧数据）。与设计的出入（`plan_groups` 加 `name`、主键为 `(plan_id, id)`、组和规则可软删除）已采纳，
  storage.md 和 ui.md §2 按实现写成。软删除的记录还不会被清理。
- B2：拼装、方案 JSON（第 1 版）、地区表（26 个地区）、GEO 数据脚本和兜底分类清单；engine 自己运行 GEO 自动
  更新（更新后要等下一次 Start 或 Reload 才生效）。重名改为直接改节点名，成员在内核里算好后用精确 `filter`
  列出，见 routing.md 8.1 节。空的自建组也改走节点选择并警告；订阅自带的 `proxy-providers` 不进节点池。
- 遗留给后续：`RULE-SET`、`SUB-RULE` 不能作为规则条目的匹配项（M8b 的"原始规则"要处理）；升级用户的分流方式
  还没选过，默认给哪种在 C1 定；GEO 数据全量随安装包附带（D18）；都只用本机代理节点验证过，没用真实订阅跑过。

### 改名为 Solaris（已完成，2026-10-30）

产品名和应用名定为 Solaris（D1）。项目未发布，所有标识直接替换，不保留旧名的兼容。"mihomo"指内核时保持不变。

| 项目 | 新值 |
| --- | --- |
| 显示名、数据目录 | `Solaris`（`~/Library/Application Support/Solaris/`、`%LOCALAPPDATA%\Solaris\`、`C:\ProgramData\Solaris\`） |
| 数据库文件 | `solaris.db` |
| GitHub 仓库、Go 模块 | `Ember-Moth/Solaris`（已改名），`github.com/Ember-Moth/Solaris/src/core` |
| Python 包、命令 | `solaris`；QML 模块名 `Solaris` |
| 协议 | proto 包 `solaris.protocol`；帧 magic `SLRS` |
| Windows | 服务 `SolarisService`，界面 `Solaris.exe`，管道 `\\.\pipe\ProtectedPrefix\Administrators\solaris` |
| macOS | bundle id `dev.tschen.solaris`，系统扩展 `dev.tschen.solaris.tunnel`，iCloud 容器 `iCloud.dev.tschen.solaris` |
| 环境变量 | 前缀 `SOLARIS_`（如 `SOLARIS_LANG`、`SOLARIS_DEV_SOCKET`、`SOLARIS_E2E`） |
| 订阅下载的 User-Agent | `solaris/<版本> clash.meta` |

### 阶段 C：分流界面与云备份（C1 已完成，2026-10-30；C2 等 Google Cloud 项目）

| 编号 | 工作 | 内容 | 可并行 |
| --- | --- | --- | --- |
| C1 | M8a 界面部分 | 订阅页简化、分流页、添加规则对话框、服务清单、地区表、默认模板、立即生效与撤销（routing.md 第 4、5 节）。依赖 B1、B2 | 与 C2 并行 |
| C2 | M7 Google Drive 备份 | 快照、提供方接口、OAuth 登录、设置页"备份与同步"、恢复。前提是 Google Cloud 项目和 OAuth 客户端已建 | 与 C1 并行 |

### 阶段 D：首发前的功能（分三轮）

**第一轮**（2026-10-30 开始）：三个会话并行，改动的文件互不重叠。每个会话先开 PR，CI 全绿后由主会话合并。

| 工作 | 内容 | 前提 |
| --- | --- | --- |
| M8b 分流进阶 | `PreviewGroup`、`MatchRule`、`ConvertToPlan`、`ExportPlan`（协议字段 51–54）；策略组编辑器的全部条件与实时预览、规则测试、搜索与按目标筛选、以此创建方案、导入导出、查看生成的配置。本轮只有它改控制协议 | 无 |
| M5 收尾 | `MacExtensionHost`；把 Nuitka 产物、管理库和系统扩展组装成 `Solaris.app`；扩展的 GEO 数据；签名与公证的发布工作流（手动触发，经 `release` 环境审批）。不安装、不激活系统扩展，实测留到第三轮 | 无 |
| M6 先行项 | 检查更新与下载（D11）、诊断信息导出、第三方许可证清单、一键导入链接的处理（D6；系统注册随打包） | 无 |

**第二轮**（第一轮合并后）：

- **M9 WARP 出口**：内核（注册、`RegisterWarp`、拼装）和界面两个会话并行。放在第二轮，是因为它和 M8b 都改拼装、
  控制协议和分流页（[privacy.md](privacy.md) 第 9 节）。
- **M7 备份的本地部分**：快照的导出、导入与合并，`BackupProvider` 接口，本地目录提供方。Google Cloud 项目建好后
  接上 Google Drive（C2）。

**第三轮**（等环境）：

- **M4 Windows 产品化**（含 B3）：需要 Windows 虚拟机。
- **M5 实测**：系统扩展审批、utun、四种栈、D8、`includeAllNetworks`；之后做 M7 的 iCloud 部分。需要测试 Mac。
- **M6 收尾与首发**：Windows 签名方案、发布全流程。

### 首发之后

- **M10 Tor 出口**（实验功能）：arti 经 pyo3 运行在界面进程，见 [privacy.md](privacy.md)。

### 需要你提供或决定的

| 事项 | 最晚何时 | 用途 |
| --- | --- | --- |
| 单独的测试 Mac 或 macOS 虚拟机 | 第一轮的 M5 收尾合并后 | 系统扩展实测、D8、`includeAllNetworks`、iCloud |
| 一个只有 TCP 的真实节点 | 第二轮 M9 验收 | 验证 WARP 出口的 MASQUE over HTTP/2 |
| Google Cloud 项目与"桌面应用"类型的 OAuth 客户端 | 第二轮（同意屏幕审核要时间，越早越好） | Google Drive 备份 |
| Windows 测试机或虚拟机 | 第三轮 M4 开始前 | M4 的安装、wintun、DNS 泄漏测试 |
| Windows 代码签名方案 | 第三轮 M6 | Authenticode 签名、服务对客户端的签名校验 |
| D5b、D8 | 见第 5 节 | — |

Apple 的签名材料（证书、两份描述文件、公证 API Key）已齐，见 apple/README.md。

## 3. 里程碑

### M1 内核跑通（Mac 开发模式，不建 TUN）

**状态**：Go 侧和 A1（CI）已合并；A2 端到端联调已完成（2026-10-29），验证了下面前两项。联调里的节点是本机
起的 HTTP 代理，还没用真实订阅的节点跑过。

**已完成**：
- engine 基于 mihomo v1.19.33，支持无 TUN 和自管 TUN 两种模式；
- 配置准入流水线、覆写，§3.3 五条安全要求各有回归测试；
- 开发模式护栏（开发服务拒绝以 root 运行）；
- 全部内核方法和 `PollEvents` 订阅；
- 50 次启停无泄漏；
- Lima 虚拟机里的网络命名空间 TUN 测试（四种栈各跑三次，全部通过）；
- D3 的体积实测数据，见 architecture.md §3.4。

**验收**：
- 普通用户身份运行的开发服务能用真实配置启动内核，`curl -x` 走代理成功，本机网络不变；
- 界面连上开发服务后，各页显示真实数据；
- CI 的网络命名空间 TUN 测试通过。

### M2 配置管理

**状态**：已完成。A2 用真实开发服务走通了导入、校验、连接、订阅更新后原地重载。

**已完成**：
- 文件存储的 `ProfileStore`；
- 订阅下载、自动更新和 `Subscription-Userinfo` 解析；
- 导入本地文件；
- 覆写（全局一份，每个配置各一份）；
- 配置页，以及删除确认。

**验收**：导入订阅 → 校验 → 使用 → 连接 → 订阅更新后原地重载。

### M3 其余页面与日常功能

**状态**：已完成。A3 补上了 qmllint、无障碍和焦点问题；Windows 上的界面验收和真实读屏软件测试还没做。

**已完成**：
- 连接页、日志页、设置页；
- 托盘（Qt.labs.platform）、单实例、关闭窗口的行为、登录时打开（平台抽象，测试中只用替身）；
- 深色主题和运行中切换外观；
- 运行中切换语言；
- 打包 Inter 和 JetBrains Mono 字体。

**验收**：六页在浅色和深色下都与原型和 DESIGN.md 一致；只用键盘能完成全部操作；关闭窗口后可从托盘恢复。

### M4 Windows 服务产品化

目标：在全新的 Windows 上完成安装、连接、开机恢复、升级和卸载的完整流程。

范围：

- **属主管理**：`Claim` / `TakeOver`；按 SID 划分 home 目录并设置 ACL；`owner.json`；属主注销时停止隧道并在下次登录时恢复（D5），管理员接管（D5b）。
- **持久化与开机恢复**：`was_running`、`overrides.json` 和节点选择（`store-selected`）。
- **系统代理**：模拟属主会话的 token 写入用户的 HKCU，停止时还原（architecture.md §5.5）。
- **客户端校验补全**：锁定文件身份；发布构建校验 Authenticode 签名者（architecture.md §5.3）。
- **TUN 栈**：带 `with_gvisor` 构建，四种栈（system、mips、gvisor、mixed）都提供；设置页加"TUN 协议栈"选项（本机
  设置，映射到配置的 `tun.stack`），默认值按实测结果定（D3）。
- **实测**：wintun、strict-route、DNS 泄漏，四种栈逐一验证；登录时打开的计划任务。
- **打包**：Nuitka standalone 构建脚本（包括 QML 目录、字体、翻译）；NSIS 安装器负责安装、升级、卸载，参考 hypervpn 的 `packaging/windows/`。

验收：在全新的 Windows 虚拟机上依次完成以下步骤，并且 DNS 泄漏测试通过：
1. 安装后连接；
2. 关闭界面，隧道仍在运行；
3. 重启系统，隧道自动恢复；
4. 升级；
5. 卸载后不留服务和运行文件。

### M5 macOS NetworkExtension

目标：发布形态的 .app 在全新的 Mac 上通过系统扩展提供隧道。

**状态**：第一步已完成（2026-10-30）。XcodeGen 工程（`apple/project.yml`）、`SolarisManager.dylib` 的 C 接口、
PacketTunnel 系统扩展骨架、`scripts/build_apple.py`（签名与不签名两种模式）、CI 的不签名构建；签名材料齐全，
本机签名校验通过。第二步 fd 模式已完成（2026-10-30）：engine 的 fd 模式、扩展找到 utun 描述符后交给 Go
（`solaris_attach_tun` / `solaris_detach_tun`）、扩展带 `with_gvisor` 构建、CI 在网络命名空间里测 fd 模式。
剩下：`MacExtensionHost`、"包含所有网络"设置（D19）、把 GEO 数据放进扩展的数据目录、组装 .app、公证与发布
工作流、测试 Mac 实测（找 utun 描述符、四种栈、重连时描述符的归属、`includeAllNetworks`、D8）。
部署目标暂定 macOS 15.0。

范围：

- **前置条件**：签名材料已齐（apple/README.md）；还差一台单独的测试 Mac 或 macOS 虚拟机。系统扩展会创建 utun，不在开发机上运行。
- **Xcode 工程**：管理库（C ABI）和 PacketTunnel 系统扩展；xcframework 由 `src/core/apple` 编出，包含 engine 的 fd 模式和控制分发器。
- **架构**：先只做 arm64（D2）。
- **扩展内部**：获取 utun fd，设置隧道网络和 DNS，建立 provider message 通道。配置和 Windows 一样经 `Start` /
  `Reload` 传入（D7）；单条消息的大小上限（D8）先用原型验证，不够就分片。
- **Python 侧**：实现 `MacExtensionHost`，通过 ctypes 调用管理库。
- **包含所有网络**（D19）：设置页加 macOS 专属的开关，默认关闭；管理库按它写 VPN 配置的 `includeAllNetworks`
  和 `excludeLocalNetworks`；扩展在 attach 时把它告诉 engine，engine 在它打开时把 `system`、`mixed` 栈换成 gVisor。
- **打包**：把 Nuitka 产物、管理库和系统扩展组装成 .app，签名并公证；实测登录项（`SMAppService`）。

验收：在全新的 Mac 上安装公证后的 .app → 批准系统扩展 → 连接 → 退出 App 后隧道仍在 → 重新打开 App 能读到状态。

### M6 发布准备

- **命名**：产品名已定为 Solaris（D1），代码里的标识已全部替换（2026-10-30）。
- **签名与发布**：准备 Windows Authenticode 和 Apple Developer ID 证书；建立发布 CI，负责构建、签名和生成校验和。
- **诊断**：诊断信息导出、崩溃日志。
- **订阅一键导入**（D6）：注册 `solaris://install-config?url=…` 和机场网站常用的 `clash://install-config`，打开后进入
  导入流程，导入前照常校验。
- **检查更新与下载**（D11）：从 GitHub Releases 检查新版本并下载安装包，校验签名后由用户确认安装，不静默安装。
- **许可合规**：发布包附带 LICENSE 和第三方许可证清单（Qt、字体等），并说明对应源代码的获取方式。

### M7 数据存储与云备份

目标：用户数据存进 SQLite；换设备后登录 Google 或 iCloud，就能恢复订阅、覆写、分流方案和设置。设计见
[storage.md](storage.md)。分三部分实施：

- **存储层**（B1）：SQLite 与 schema 迁移；`ProfileStore` 和设置存储改为基于数据库。
- **Google Drive 备份**（C2）：
  - 快照的导出、导入和按记录合并；
  - 自动备份（去抖、哈希比较），云端保留 10 份；
  - 应用内 OAuth 登录（loopback + PKCE）、appDataFolder；
  - 设置页"备份与同步"分组，开启时明确提示备份不加密、包含订阅链接。
- **iCloud**（M5 之后）：在管理库里用 CloudKit 私有数据库实现提供方，仅 macOS。

验收：
1. 在一台机器上导入几份订阅、覆写和分流方案，登录 Google 后自动备份；
2. 在另一台机器上（或清空数据目录后）登录同一账户并恢复，全部还原，订阅重新下载并通过校验；
3. macOS 上用 iCloud 走一遍同样的流程。

### M8a 自定义分流：基础

目标：订阅可以只提供节点，分流规则由用户自己维护；新手导入即用，想调整时从"让某个服务走某个地区"开始。设计见
[routing.md](routing.md)（第二版）。内核部分是 B2，界面部分是 C1。

**状态**：已完成（2026-10-30）。内核部分 B2、界面部分 C1 都已合并；e2e 覆盖了验收的三步。只用本机代理节点验证过，
没用真实订阅跑过。

范围（routing.md 第 10 节）：

- **分流方式**："订阅自带 / 我的方案"全局开关，首次导入时自动选择；订阅页简化；概览页显示"当前使用"。
- **节点池**：加入与排除、信息节点过滤、重名处理、订阅失效时沿用上次的节点。
- **地区组与模板**：地区识别、自动生成地区组、默认模板、服务清单（约 40 个服务）。
- **规则**：
  - 服务、分类、域名、IP 段四类条目；
  - 启用、拖动、删除与撤销、兜底；
  - 方案的新建、复制、重命名、删除。
- **生效**：每次确认的改动先校验再立即生效，连续操作合并为一次。
- **内核**：在内核里拼装配置（D16）；`InspectSource`、`ListGeoCategories`；安装包附带初始 GEO 数据（D17）。

验收：
1. 导入一份只有节点的订阅 → 自动用默认方案连接；
2. 添加"Telegram → 日本"后，新的 Telegram 连接走日本节点，连接页显示规则"Telegram"；
3. 再导入一份带规则的订阅，切到"订阅自带"，流量按订阅的规则走。

### M8b 自定义分流：进阶

- **策略组**：编辑器的全部条件和实时预览（`PreviewGroup`）。
- **排查**：规则测试（`MatchRule`）、规则搜索与筛选、连接页显示规则条目名称、查看生成的配置。
- **迁移与分享**："以此创建方案"、导入、导出（`ConvertToPlan`、`ExportPlan`）。

验收：
- 把一份 400 条规则的订阅转成方案，摘要准确；
- 新建"排除 0.1 倍率节点"的策略组，编辑器里的预览与连接后代理页显示的成员一致；
- 规则测试对 20 个样例域名的结果，与实际连接命中的规则一致。

### M9 WARP 出口

目标：流量经用户的节点再进入 Cloudflare WARP，机场看不到访问的网站，网站看不到节点 IP。设计见
[privacy.md](privacy.md) 第 3 节（D22）。

范围：

- **内核**：`internal/warp` 经"经由"组注册账户（`RegisterWarp`）；拼装 MASQUE 或 WireGuard 出站，`dialer-proxy`
  指向经由组；出口不可用时拒绝连接，并给出 `egress_unavailable`、`via_empty` 警告。
- **界面**：分流页的"隐私出口"一节、出口设置对话框、规则目标、账户存进系统凭据库、模拟宿主。

验收：
1. 经一个只有 TCP 的真实节点打开 WARP 出口，注册成功；
2. "ifconfig.me → WARP 出口"返回 Cloudflare 的 IP，而不是节点 IP；
3. 删除账户后，同一规则的连接被拒绝；
4. 节点支持 UDP 时，换成 WireGuard 结果相同。

### M10 Tor 出口（首发后，实验功能）

目标：流量经用户的节点进入 Tor 网络，不需要网桥，可以访问 `.onion`。设计见 [privacy.md](privacy.md) 第 4 节（D23）。

范围：

- `src/tor`：arti-client 的 pyo3 模块，上游连接只经内核的 `tor-upstream` 入站，自带 SOCKS5 服务，按目标主机隔离线路；
- 构建：maturin 进 Python 的 CI 和两个平台的打包；
- 界面：设置页"实验功能"、生命周期、出口行的连接进度；
- 内核：拼装 `tor-upstream` 入站和"Tor 出口"出站；服务清单加"Onion 网站"。

验收：一分钟内连上 Tor 网络；check.torproject.org 显示正在使用 Tor；能打开 `.onion` 网站；退出界面后同一规则的
连接被拒绝；抓包确认 arti 没有直连流量。

## 4. 协作方式

- **本机开发不改动本机网络**：开发机上不创建 TUN，不设置系统代理，不修改路由和 DNS，也不在本机真实注册登录项或计划任务。界面用模拟宿主或无 TUN 的开发服务调试；需要 TUN 的测试放到 CI 的网络命名空间或测试虚拟机里做。
- **先设计，再开工**：大的功能先写设计文档（如 storage.md、routing.md），原型跟着设计修改，定稿后再拆成会话实现。
- **并行会话按文件划分**：每个会话写明可以改哪些文件，避免两个会话改同一处；协议、翻译目录、`Main.qml`、`composition.py` 这类共享文件，由主会话在合并时统一处理。
- **协议变更**：先改 `control.proto`，运行 `scripts/gen_protocol.py`；在同一个提交里同步修改 Go 分发器、Python `ServiceHost` 和模拟宿主，并用共享样本守住两端一致。
- **视觉变更**：先改 DESIGN.md，再运行 `scripts/gen_theme.py`；原型画布同步修改。
- **文案变更**：中文是源语言；改动文案后运行 `scripts/gen_i18n.py`，并在 `en.ts` 里补齐英文。
- **mihomo 升级**：钉在 `Meta` 分支的 tag 上；升级前跑完整测试，并按 architecture.md §3.3 逐条复核安全要求。

## 5. 决策

### 5.1 待决策

按最晚需要决定的时间排列，越早决定，返工越少。

| # | 事项 | 最晚何时 | 影响 |
| --- | --- | --- | --- |
| D5b | 内核运行中能否由管理员接管其他用户的隧道（倾向可以，需 UAC 确认） | M4 | 服务的属主逻辑 |
| D8 | provider message 单条大小上限 | M5 原型 | `Reload` 能否直接携带大配置 |

### 5.2 已决定（2026-10-29 至 10-30）

| # | 决定 | 详见 |
| --- | --- | --- |
| D4 | 覆写只做 Merge，不做 Script；一份全局覆写，加上每个配置各一份 | ui.md §4 |
| D9 | 深色主题放在 M3，先补 DESIGN.md 再实现 | ui.md §6 |
| D10 | 许可证：GPL-3.0-only | LICENSE |
| D12 | 界面提供简体中文和英文，中文为源语言 | ui.md §5 |
| D13 | 本地用 SQLite；云端只做备份与恢复，不做双向同步；快照不加密；Google Drive 在应用内登录，用 appDataFolder；iCloud 用 CloudKit，仅 macOS | storage.md |
| D15 | 自定义分流（第二版）：<br>• 全局开关"订阅自带 / 我的方案"，首次导入时自动选择；<br>• "我的方案"下所有订阅组成节点池，可单独排除；<br>• 可以有多套方案；<br>• 规则条目按意图记录，默认按服务添加；<br>• 第一版规则类型为 GEOSITE / GEOIP、域名、IP 段 | routing.md |
| D16 | 分流配置在内核里拼装，节点筛选和规则匹配都用 mihomo 自己的逻辑 | routing.md 第 8 节 |
| D17 | GEO 分类列表由内核从正在使用的 GEO 数据里读取；安装包附带初始 GEO 数据，另附一份分类名清单作兜底 | routing.md 第 8 节 |
| D1 | 产品名与应用名都是 Solaris，定稿不再改变。GitHub 仓库改为 `Ember-Moth/Solaris`，macOS bundle id 前缀 `dev.tschen.solaris` | 本文第 2 节"改名" |
| D14 | Google 令牌存放在系统凭据库（macOS 钥匙串、Windows 凭据管理器），新增 `keyring` 依赖 | storage.md 第 4 节 |
| D18 | GEO 数据全量随安装包附带（geosite 4.4 MB、geoip 16.5 MB） | routing.md 第 8 节 |
| D2 | 构建架构：Windows 只做 x64，macOS 先只做 arm64 | 本文 M4、M5 |
| D3 | Windows 带 `with_gvisor` 构建，四种 TUN 栈都提供，由用户在设置里选择；macOS 系统扩展同样带 `with_gvisor`，fd 模式默认 gVisor 栈 | architecture.md §3.4 |
| D5 | 属主注销时停止隧道，下次登录后恢复 | architecture.md §5.4 |
| D6 | 订阅一键导入注册 `solaris://install-config?url=…` 和 `clash://install-config` | 本文 M6 |
| D7 | macOS 的配置和 Windows 一样，经控制协议的 `Start` / `Reload` 传入 | architecture.md §6.2 |
| D11 | 内置检查更新与下载：安装包来自 GitHub Releases，校验签名，由用户确认安装 | 本文 M6 |
| D19 | macOS 扩展的 `includeAllNetworks` 默认关闭，用户可在设置页打开；打开后 `system`、`mixed` 栈换成 gVisor | architecture.md §3.4、ui.md §8 |
| D20 | fd 模式的 `dns.fake-ip-range` 固定为 `198.18.0.1/16`（TUN 地址由它得出）；用户关掉 `ipv6` 时，进入隧道的 IPv6 流量被丢弃 | architecture.md §3.1 |
| D21 | 隐私出口只在"我的方案"下提供；出口不可用时，指向它的规则拒绝连接，不改走普通代理 | privacy.md 第 1 节 |
| D22 | WARP 出口放在内核：mihomo 的 MASQUE 或 WireGuard 出站，`dialer-proxy` 经用户的节点，默认 MASQUE over HTTP/2；内核经节点注册账户，账户存系统凭据库、不进备份；首发前做（M9） | privacy.md 第 3 节 |
| D23 | Tor 出口用 arti-client，经 pyo3 运行在界面进程；首发后作为实验功能（M10）；退出界面后 Tor 不可用，连接被拒绝 | privacy.md 第 4 节 |

## 6. 风险

| 风险 | 应对 |
| --- | --- |
| CI 变红后新问题被已有的失败掩盖 | 任何合并都以 CI 全绿为前提；并行会话合并前先在 PR 上跑 CI |
| 模拟宿主与真实服务的行为再次出现出入 | e2e 测试核对两者对配置的判定；新增宿主方法时同时补 e2e 用例 |
| mihomo 以全局状态运行，反复启停可能泄漏资源 | 启停压力测试；Stop 后核对 goroutine 和文件描述符 |
| mihomo 内部接口（`hub`、`executor`、`tunnel`）随版本变化 | 钉住 tag；engine 只通过少量入口调用 mihomo，升级时集中修改 |
| Windows 上 wintun、strict-route 的行为差异和 DNS 泄漏 | M4 专项实测，形成检查清单 |
| 开发机不跑 TUN，TUN 相关问题发现得晚 | CI 的网络命名空间测试；M4、M5 尽早准备测试虚拟机 |
| 系统扩展的审批、签名和公证流程复杂 | M5 先做最小可用的扩展，再补全功能 |
| 只在 macOS 上看过界面渲染 | A3 起在 Windows 上做界面验收 |
| 云端快照不加密，订阅链接会随云端账户一起泄露 | 开启备份时明确提示；只申请 appDataFolder 权限；快照不含令牌 |
| Google OAuth 同意屏幕审核耗时 | 尽早建 Google Cloud 项目并提交审核 |
| CloudKit 依赖签名、entitlement 和 M5 的管理库 | iCloud 放在 M7 的最后，与 M5 一起验证 |
| WARP 的注册接口不是官方的，Cloudflare 可能改动或限流 | 常量集中在 `internal/warp`；失败时拒绝连接，不退回普通代理；功能默认关闭 |
| arti-client 每月发一个不兼容版本；Rust 工具链进入构建 | Tor 放到首发后，作为实验功能；钉住版本，每季度升级 |

## 7. DESIGN.md 待补规则

目前没有。M3 和原型阶段提出的规则已由 A3 和 C1 写进 DESIGN.md；英文文案里"接管方式"译为 "Inbound"。

## 8. 文档一览

| 文档 | 内容 |
| --- | --- |
| [architecture.md](architecture.md) | 系统架构：engine、控制协议、Windows 服务、macOS 扩展、安全模型、构建与测试 |
| [ui.md](ui.md) | Python 界面：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为 |
| [storage.md](storage.md) | 计划中的 SQLite 存储与云备份（M7） |
| [routing.md](routing.md) | 计划中的自定义分流（M8） |
| [privacy.md](privacy.md) | 计划中的隐私出口：WARP（M9）和 Tor（M10） |
| [DESIGN.md](../DESIGN.md) | 视觉规范，Theme.qml 由它生成 |
| roadmap.md（本文） | 进度、计划、决策与风险 |
