# 开发方案

本文规划从当前骨架到首个可发布版本的工作：分几个里程碑、按什么顺序做、每一步怎样算完成、哪些决定要在什么时候做出。
设计细节见 [architecture.md](architecture.md)，视觉规范见 [DESIGN.md](../DESIGN.md)。每完成一个里程碑更新本文。

撰写于 2026-10-29，对应提交 `bca73c0`；同日 M1、M2 合并后更新，对应提交 `c7e58d5`。

## 1. 当前进度

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| 架构与视觉规范 | 完成 | architecture.md、DESIGN.md；6 页原型在 Claude Design 画布上 |
| 控制协议 | v1 | 消息、方法表、帧编解码已定，Go 与 Python 有共享样本测试；覆写为 `repeated string overrides` |
| Go 控制分发器与服务入口 | 部分 | 全部内核方法、Start / Stop / Reload / Validate、`PollEvents` 订阅；`Claim`、系统代理、Authenticode 校验留到 M4 |
| engine（内嵌 mihomo） | 完成（M1） | mihomo v1.19.33；无 TUN 与自管 TUN 模式、覆写、§3.3 安全项；fd 模式留到 M5 |
| Python 宿主层 | 完成（M2） | 控制客户端、全部请求映射（配置随 Validate / Start / Reload 提交）、模拟宿主 |
| 界面 | 部分 | 六页全部可用；菜单与确认框；覆写编辑；中英双语；浅色与深色；托盘、单实例、登录时打开；qmllint 与无障碍检查未做 |
| macOS 原生部分 | 未开始 | 只有 Go 侧的 C archive 导出 |
| 打包与安装 | 未开始 | 只有 Nuitka 入口文件 |
| CI | 完成 | Python（macOS、Windows）与 Go（macOS、Windows、Linux）检查、测试和生成文件校验；Linux 网络命名空间里的 TUN 测试（只在 Lima 虚拟机里跑过，GitHub 上未运行） |

## 2. 总体顺序

工作分两条线并行推进，最后在发布准备汇合：

- **内核线**：M1 engine 跑通 → M4 Windows 服务产品化、M5 macOS NetworkExtension（两者互相独立）。
- **界面线**：M2 配置管理 → M3 其余页面与日常功能 → M7 数据存储与云备份 → M8a / M8b 自定义分流。界面线全程对着
  模拟宿主开发，不必等内核线。M7、M8 的界面先在原型里设计。M8a 依赖 M7 的 SQLite 存储层，M7 的云备份可以和 M8 并行。
- **汇合**：M6 发布准备，依赖 M3、M4、M5、M7、M8a、M8b。M7 的 iCloud 部分依赖 M5。

M1 是关键路径：真实流量、配置校验、订阅数据都依赖它。

| 里程碑 | 依赖 | 前置条件 | 规模 |
| --- | --- | --- | --- |
| M1 内核跑通（Mac 开发模式，不建 TUN） | — | — | 大 |
| M2 配置管理 | M1 的 `Validate` / `Reload`（存储和界面可先做） | — | 中 |
| M3 其余页面与日常功能 | M1 的订阅接口（可先对着模拟宿主做） | — | 中 |
| M4 Windows 服务产品化 | M1 | Windows 测试机或虚拟机 | 大 |
| M5 macOS NetworkExtension | M1 | Apple Developer 账号与 NE entitlement；单独的测试 Mac 或虚拟机 | 大 |
| M6 发布准备 | M3、M4、M5、M7、M8a、M8b | 代码签名证书 | 中 |
| M7 数据存储与云备份 | M3（设置存储）；iCloud 部分依赖 M5 | Google Cloud 项目与 OAuth 客户端；iCloud 需要 Apple Developer 账号；原型 v2 | 中 |
| M8a 自定义分流：基础 | M7 的 SQLite 存储层；M1 | 原型按 routing.md 第二版修订 | 大 |
| M8b 自定义分流：进阶 | M8a | — | 中 |

## 3. 里程碑

### M1 内核跑通（Mac 开发模式，不建 TUN）

状态：Go 侧完成，2026-10-29 合并。剩余验收：界面连开发服务的端到端联调；GitHub CI 上的 TUN job。

目标：在 Mac 上以开发模式运行服务，经回环地址上的混合端口跑通真实代理流量；界面连上开发服务后，概览和
代理两页显示真实数据。开发机上不创建 TUN、不改系统代理、路由和 DNS（architecture.md §11）；自管 TUN
模式在 CI 里测试。

范围：

- **engine**（`src/core/internal/engine`，architecture.md §3）：
  - 初始化：按属主设置 home 目录，开启 embed mode；
  - 配置准入流水线：先实现无 TUN 模式和自管 TUN 模式；
  - Start / Reload / Stop；状态与模式；代理组、选择节点、测速；provider 与规则。
- **开发模式护栏**：开发服务强制使用无 TUN 模式；用户配置里的 TUN、自动路由等项被清除，入站只监听回环
  地址；请求开启 TUN 或系统代理时返回 `unsupported_method`。每一条都有测试。
- **TUN 测试**：在 CI 的 Linux runner 上，于独立的网络命名空间内用自管模式启动 TUN，验证配置流水线和
  启停流程。
- **订阅缓冲**：流量、连接、日志三种订阅，按 §4.4 做有界缓冲和 30 秒过期，实现 `PollEvents`。
- **分发器**：实现全部内核方法，以及 Start / Stop / Reload / Validate（开发模式下也提供宿主方法）。
- **安全要求**：§3.3 的五条要求，每条一个回归测试。
- **协议**：Start 携带配置；Python 侧 `TunnelHost.start` 改为接收配置，模拟宿主同步修改。
- **实测**：构建标签组合与二进制体积（决策 D3）。

验收：

- 用一份真实的 mihomo 配置（即使配置里开启了 `tun`），以普通用户身份运行的开发服务能启动内核；
  `curl -x http://127.0.0.1:<混合端口>` 经代理访问成功；本机的网络接口、路由表、系统代理和 DNS 均无变化。
- 设置 `MIHOMO_DESKTOP_DEV_SOCKET` 后启动界面，概览和代理两页显示真实数据，能切换模式和节点；
  TUN 和系统代理开关给出"开发模式不支持"的提示。
- CI 的网络命名空间里，自管 TUN 模式能启动并经 TUN 转发流量。
- 连续启停 50 次后，goroutine 数和文件描述符数回到初始水平。

### M2 配置管理

状态：界面侧完成（含覆写），2026-10-29 合并；目前只对着模拟宿主验证过，端到端联调随 M1 一起做。

目标：用户能导入订阅或本地配置，校验后使用，并在订阅更新后原地重载。

范围：

- **存储**：`ProfileStore` 存放在用户数据目录（`%LOCALAPPDATA%\<Product>\`、`~/Library/Application Support/<Product>/`）。
- **订阅**：订阅下载与定时自动更新；解析 `Subscription-Userinfo` 响应头，得到用量和到期时间；支持导入本地文件。
- **覆写**：只做 Merge，一份全局覆写加上每个配置各一份（D4，architecture.md §7.4）。
- **与服务的交互**：导入前用服务的 `Validate` 校验；切换配置或订阅更新时走 `Reload`。
- **界面**：按原型实现配置页；概览页的配置分组和侧栏的配置名称接入真实数据。

验收：导入订阅链接 → 校验 → 使用 → 连接 → 订阅更新后原地重载，全程不断开 TUN。

### M3 其余页面与日常功能

目标：六个页面全部可用，具备桌面客户端的日常行为。

状态：页面、深色主题、设置与桌面行为已合并（2026-10-29）。剩余：qmllint 加入 CI、无障碍检查、M3 会话提出的
DESIGN.md 补充规则。

范围：

- **页面**（按原型）：
  - 连接页：连接订阅、搜索、按 TCP/UDP 筛选、关闭连接；
  - 日志页：日志订阅、级别过滤、暂停、导出；
  - 设置页：设置项映射到 `RunOptions` 和覆写，并持久化。
- **托盘**：显示状态，可连接/断开、切换模式、打开窗口。
- **桌面行为**：单实例；关闭窗口时的行为；登录时启动（Windows 用计划任务，macOS 用登录项）。
- **深色主题**（D9）：先在 DESIGN.md 补齐深色 token 和规则，再生成 Theme.qml 并实现；默认跟随系统外观，设置页可选跟随系统、浅色或深色；原型画布补深色画板。
- **打磨**：打包 Inter 与 JetBrains Mono；qmllint 加入 CI；无障碍检查（键盘遍历、读屏名称）。

验收：六页在浅色和深色下都与原型和 DESIGN.md 一致；只用键盘能完成全部操作；关闭窗口后可从托盘恢复。

### M4 Windows 服务产品化

目标：在全新的 Windows 上完成安装、连接、开机恢复、升级和卸载的完整流程。

范围：

- **属主管理**：`Claim` / `TakeOver`；按 SID 划分 home 目录并设置 ACL；`owner.json`；会话注销时的策略（D5）。
- **持久化与开机恢复**：`was_running` 和节点选择（`store-selected`）。
- **系统代理**：模拟属主会话的 token 写入用户的 HKCU，停止时还原（§5.5）。
- **客户端校验补全**：锁定文件身份；发布构建校验 Authenticode 签名者（§5.3）。
- **实测**：wintun、strict-route、DNS 泄漏；确定 TUN 栈（D3）。
- **打包**：Nuitka standalone 构建脚本；NSIS 安装器负责安装、升级、卸载，参考 hypervpn 的 `packaging/windows/`。

验收：在全新的 Windows 虚拟机上依次完成：

1. 安装后连接；
2. 关闭界面，隧道仍在运行；
3. 重启系统，隧道自动恢复；
4. 升级；
5. 卸载后不留服务和运行文件。

DNS 泄漏测试通过。

### M5 macOS NetworkExtension

目标：发布形态的 .app 在全新的 Mac 上通过系统扩展提供隧道。

范围：

- **前置条件**：Apple Developer 账号、Team ID、NE entitlement；一台单独的测试 Mac 或 macOS 虚拟机。
  系统扩展会创建 utun，不在开发机上运行。
- **Xcode 工程**：管理库（C ABI）和 PacketTunnel 系统扩展；xcframework 由 `src/core/apple` 编出，包含 engine 的 fd 模式和控制分发器。
- **扩展内部**：获取 utun fd、设置隧道网络和 DNS；provider message 通道。决策 D7 和 D8 先用原型验证。
- **Python 侧**：实现 `MacExtensionHost`，通过 ctypes 调用管理库。
- **打包**：把 Nuitka 产物、管理库和系统扩展组装成 .app，签名并公证。

验收：在全新的 Mac 上安装公证后的 .app → 批准系统扩展 → 连接 → 退出 App 后隧道仍在 → 重新打开 App 能读到状态。

### M6 发布准备

范围：

- **命名**：确定产品名（D1），替换代码里的工作名标识。
- **签名与发布**：准备 Windows Authenticode 和 Apple Developer ID 证书；建立发布 CI，负责构建、签名和生成校验和。
- **诊断**：诊断信息导出、崩溃日志。
- **按决策实施**：自动更新（D11）。
- **许可合规**：发布包附带 LICENSE 和第三方许可证清单（Qt、字体等），并给出对应源代码的获取方式。

### M7 数据存储与云备份

目标：用户数据存进 SQLite；换设备后登录 Google 或 iCloud 即可恢复订阅、覆写和设置，不用逐条重新添加。设计见
[storage.md](storage.md)。

范围：

- **存储层**：SQLite 数据库与 schema 迁移；`ProfileStore` 和设置存储改为基于数据库；从 M2 的文件存储和临时设置文件
  一次性导入。
- **快照**：导出、导入、按记录合并；自动备份（去抖、哈希比较）、云端保留 10 份。
- **Google Drive**：应用内 OAuth 登录（loopback + PKCE），appDataFolder，令牌存系统凭据库（D14）。
- **设置页**："备份与同步"分组，开启时明确提示备份不加密、包含订阅链接。
- **iCloud**（M5 之后）：在管理库里用 CloudKit 私有数据库实现提供方，仅 macOS。

验收：在一台机器上导入几份订阅和覆写 → 登录 Google → 自动备份；在另一台机器上（或清空数据目录后）登录同一账户 →
恢复后订阅、覆写和同步范围的设置全部还原，订阅重新下载并通过校验。macOS 上用 iCloud 走同样的流程。

### M8a 自定义分流：基础

目标：订阅可以只提供节点，分流规则由用户自己维护；新手导入即用，想调整时从"让某个服务走某个地区"开始。
设计见 [routing.md](routing.md)（第二版）。

范围（routing.md 第 10 节）：

- **分流方式**：全局开关"订阅自带 / 我的方案"，首次导入时自动选择；订阅页简化，只管来源；概览页显示"当前使用"。
- **节点池**：加入与排除、信息节点过滤、重名处理、订阅失效时沿用上次的节点。
- **地区组与模板**：地区识别、自动生成地区组、默认模板、服务清单（约 40 个服务）。
- **规则**：服务、分类、域名、IP 段四类条目；启用、拖动、删除与撤销、兜底；方案的新建、复制、重命名、删除。
- **生效**：每次确认的改动先校验再立即生效，连续操作合并。
- **内核**：在内核里拼装配置（D16）；`InspectSource`、`ListGeoCategories`；安装包附带初始 GEO 数据（D17）。
- **存储与备份**：分流方式和方案进入 SQLite 和备份快照。

验收：导入一份只有节点的订阅 → 自动用默认方案连接 → 添加"Telegram → 日本"后，新的 Telegram 连接走日本节点
（连接页显示规则"Telegram"）→ 再导入一份带规则的订阅，切到"订阅自带"，流量按订阅的规则走。

### M8b 自定义分流：进阶

范围：

- **策略组**：编辑器的全部条件（来源订阅、关键字或正则、手动挑选、其他成员、排除）和实时预览（`PreviewGroup`）。
- **排查**：规则测试（`MatchRule`）、规则搜索与筛选、连接页显示规则条目名称、查看生成的配置。
- **迁移与分享**：从订阅"以此创建方案"、导入、导出（`ConvertToPlan`、`ExportPlan`）。

验收：把一份 400 条规则的订阅转成方案，摘要准确；新建"排除 0.1 倍率节点"的策略组，预览与连接后代理页显示的成员
一致；规则测试对 20 个样例域名的结果与实际连接的命中规则一致。

## 4. 协作方式

- **本机开发不改动本机网络**：开发机上不创建 TUN，不设置系统代理，不修改路由和 DNS。界面用模拟宿主
  或无 TUN 的开发服务调试；凡是需要 TUN 的测试，放到 CI 的网络命名空间或测试虚拟机里做。
- **协议变更**：先改 `control.proto`，运行 `scripts/gen_protocol.py` 重新生成；在同一个提交里同步修改 Go 分发器、Python `ServiceHost` 和模拟宿主，并用共享样本测试守住两端一致。
- **视觉变更**：先改 DESIGN.md，再运行 `scripts/gen_theme.py`；原型画布同步修改。
- **mihomo 升级**：钉在 `Meta` 分支的 tag 上；升级前跑完整测试，并按 §3.3 逐条复核安全要求。

## 5. 待决策事项

按"最晚需要决定的时间"排列。越早决定，返工越少。

| # | 事项 | 最晚何时 | 影响 |
| --- | --- | --- | --- |
| D6 | 订阅一键导入的 URL scheme（如 `clash://install-config`） | M2 | 深链接处理、安装器注册 |
| D3 | TUN 栈与构建标签（M1 实测数据见 architecture.md §3.4） | M4 实测后 | 兼容性、体积 |
| D1 | 产品名 | M4 安装器和 M5 bundle id 之前 | 服务名、管道名、协议 magic、bundle id、目录 |
| D2 | 支持的架构（Windows x64 / arm64，macOS arm64 / x86_64） | M4、M5 构建前 | 构建矩阵、xcframework 切片 |
| D5 | 属主注销时是否停止隧道；管理员能否接管 | M4 | 服务的属主逻辑 |
| D7 | macOS 配置传递方式 | M5 开始 | 扩展的启动流程 |
| D8 | provider message 单条大小上限 | M5 原型 | `Reload` 能否直接携带大配置 |
| D11 | 是否内置自动更新 | M6 | 发布基础设施 |
| D14 | Google 令牌的存放方式（系统凭据库需要新增 `keyring` 之类的依赖） | M7 开始前 | 依赖、打包、安全 |

已决定（均为 2026-10-29）：

- D10 许可证：GPL-3.0-only，见仓库根目录的 LICENSE。
- D4 覆写：只做 Merge，不做 Script；一份全局覆写加上每个配置各一份。
- D12 界面语言：简体中文和英文，中文为源语言（architecture.md §7.5）。
- D9 深色主题：放在 M3，先补 DESIGN.md 再实现。
- D13 数据存储与云备份：本地用 SQLite；云端做备份与恢复，不做双向同步；快照不加密；Google Drive 应用内登录，
  用 appDataFolder；iCloud 用 CloudKit，仅 macOS（storage.md）。
- D15 自定义分流（第二版修订）：全局开关"订阅自带 / 我的方案"，首次导入时自动选择；"我的方案"下所有订阅组成
  节点池，可以单独排除；可以有多套分流方案，默认一套；规则条目按意图记录，默认按服务添加；第一版规则类型为
  GEOSITE / GEOIP 分类和域名、IP 段（routing.md）。
- D16 分流配置在内核里拼装：界面提交订阅原文和方案，节点筛选、规则匹配都用 mihomo 自己的逻辑。
- D17 GEO 分类列表由内核从正在使用的 GEO 数据里读取；安装包附带初始 GEO 数据和一份分类名清单作兜底。

## 6. 风险

| 风险 | 应对 |
| --- | --- |
| mihomo 以全局状态运行，反复启停可能泄漏资源 | M1 的启停压力测试；Stop 后核对 goroutine 和文件描述符 |
| mihomo 内部接口（`hub`、`executor`、`tunnel`）随版本变化 | 钉住 tag；engine 只通过少量入口调用 mihomo，升级时集中修改 |
| Windows 上 wintun、strict-route 的行为差异和 DNS 泄漏 | M4 专项实测，形成检查清单 |
| 开发机不跑 TUN，TUN 相关问题发现得晚 | M1 起在 CI 的网络命名空间里跑自管 TUN 测试；M4、M5 尽早准备测试虚拟机 |
| 系统扩展的审批、签名和公证流程复杂 | M5 先做最小可用的扩展，再补全功能 |
| asyncio 与 Qt 的线程交互 | 界面回调一律经 `AsyncRunner` 回到 Qt 线程；ViewModel 测试覆盖 |
| 只在 macOS 上看过界面渲染 | M3 起在 Windows 上做界面验收 |
| 云端快照不加密，订阅链接随云端账户泄露 | 开启备份时明确提示；只申请 appDataFolder 权限；快照不含令牌 |
| Google OAuth 同意屏幕审核耗时 | M7 开始时就建 Google Cloud 项目并提交审核 |
| CloudKit 依赖签名、entitlement 和 M5 的管理库 | iCloud 放在 M7 的最后，与 M5 一起验证 |

## 7. 下一步

1. **端到端联调**：开发服务加界面（`MIHOMO_DESKTOP_DEV_SOCKET`），完成 M1 剩余的验收项：概览和代理两页显示
   真实数据，导入配置后经回环混合端口走代理，TUN 和系统代理开关提示"开发模式不支持"。顺带实测只改规则时
   重载是否断开已有连接（routing.md 第 12 节）。
2. **CI**：确认 GitHub 上的全部 job 通过，包括网络命名空间里的 TUN 测试。
3. **M3 收尾**：qmllint 加入 CI、无障碍检查、删除或保存后焦点回到原控件；把 M3 和原型会话提出的规则补进 DESIGN.md。
4. **原型修订**：按 routing.md 第二版修改画布，深色画板同步侧栏。
5. **M7 存储层**：SQLite 与迁移，M8a 依赖它；同时建 Google Cloud 项目和桌面应用类型的 OAuth 客户端。
6. **决策**：D1 产品名、D2 支持的架构，M4 和 M5 开始前需要定下；D14 Google 令牌的存放方式，M7 云备份开始前定下。
