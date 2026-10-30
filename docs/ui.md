# 界面

本文描述 Python 界面的结构和各部分的设计：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为。
系统整体的架构见 [architecture.md](architecture.md)，视觉规范见 [DESIGN.md](../DESIGN.md)。备份的设计见
[storage.md](storage.md)，界面部分见第 8 节（目前只有导出到文件和从文件恢复）；自定义分流的设计见
[routing.md](routing.md)，界面部分见第 9 节。

## 0. 结构

- 分层参考 hypervpn：视图 → ViewModel → models → 宿主客户端。界面用 QML，控件基于 Qt Quick Controls 的
  Basic 样式按 DESIGN.md 定制；主题由 `scripts/gen_theme.py` 从 DESIGN.md 生成。
- asyncio 事件循环运行在后台线程，结果通过排队信号回到 Qt 线程。
- **控制客户端**：负责构造 Request、校验 Response 的版本和 ID，并按间隔调用 `PollEvents` 拉取订阅。
  传输有两种实现：
  - Windows：一条长连接（`ProactorEventLoop.create_pipe_connection`），负责帧编解码、按请求 ID
    匹配响应和断线重连；
  - macOS：每个请求通过 ctypes 调用一次管理库。这是阻塞调用，放到线程池里执行；
  - 依赖只有 protobuf，不需要 HTTP 或 gRPC 库。
- **宿主抽象** `TunnelHost` 有两个实现，共用同一个控制客户端：
  - `ServiceHost`：全部方法都走 Windows 命名管道；开发模式下走本地 socket（architecture.md §11）；
  - `MacExtensionHost`：启停和控制消息都通过 ctypes 调用管理库，请求映射沿用 `ServiceHost`（apple/README.md）。

  测试和 `scripts/preview.py` 用 `tests/support/simulated.py` 里的模拟宿主。

## 1. 宿主接口里的配置

`validate`、`start`、`reload` 都接收 `TunnelConfig`，对应协议里的 `ValidateRequest`、`StartRequest`、
`ReloadRequest`：

| 字段 | 内容 |
| --- | --- |
| `config_yaml` | 配置原文，即 `ProfileStore` 里存的 YAML |
| `overrides` | 覆写的 YAML 原文，按应用顺序排列：先全局覆写，再这份配置自己的覆写；没有设置的不放进来。语义见第 4 节 |
| `composition` | "我的方案"时代替 `config_yaml`：节点池、方案 JSON、信息节点表达式和链式出口（第 9 节）；两者只能有一个 |
| `options` | `RunOptions`：`tun_enabled`、`system_proxy_enabled`、`mixed_port`（0 表示沿用配置）、`allow_lan`、`tun_stack`（空串表示沿用配置）、`include_all_networks`。`validate` 不使用。`include_all_networks` 不进控制协议，只有 `MacExtensionHost` 在启动隧道时把它写进 VPN 配置（architecture.md §6.1） |

- **Start** 的各项都取自设置（第 8 节）：TUN 和系统代理是设置页「接管方式」的选择，其余是设置页的值。宿主
  发出之前按自己能接管的改写这两项（`TunnelHost.takeover`，第 8 节）。
- **Reload** 用 `GetStatus` 报告的 TUN 和系统代理状态，所以切换配置或订阅更新都不会改变隧道的这两项；端口、
  局域网连接和 TUN 协议栈取自设置。
- 错误码：配置被拒绝时为 `invalid_config`（`message` 是 mihomo 的原始错误，界面显示在「配置无效：」之后）；
  未运行时 Reload 返回 `not_running`；运行中 Start 返回 `already_running`；端口打不开（例如混合端口被其他程序
  占用）时为 `apply_failed`，`message` 是系统的监听错误，界面显示在「配置未能生效：」之后，Reload 失败时隧道
  保持原来的配置和端口，Start 失败时状态为"连接失败"。中文提示在 `viewmodels/feedback.py`。
- 只有 DIRECT、没有任何节点的配置是合法的；策略组或规则引用了不存在的节点或策略组时，内核拒绝。

## 2. 配置存储

界面的本地数据（配置、覆写、设置、分流方案）存在用户数据目录下的 SQLite 数据库 `solaris.db` 里。用户数据
目录在 Windows 上是 `%LOCALAPPDATA%\Solaris\`，在 macOS 上是 `~/Library/Application Support/Solaris/`，
`Solaris` 取自 `product.py` 的 `DISPLAY_NAME`。数据库的整体设计和云备份见 [storage.md](storage.md)。代码分布：

- `models/database.py`：连接、表结构、迁移；
- `models/store.py`：配置和覆写（`ProfileStore`），以及打开数据库的 `open_database`；
- `models/settings.py`：设置（第 8 节）；
- `models/routing.py`：分流数据（routing_state 和方案的三张表）；
- `models/backup.py`、`models/backup_providers.py`、`models/backup_auto.py`：备份快照与恢复、提供方、自动备份
  （storage.md 第 3、6 节）；
- `models/plans.py`、`models/services.py`、`models/regions.py`：方案的默认模板与方案 JSON、服务清单、地区名称（第 9 节）。

### 2.1 表

| 表 | 内容 |
| --- | --- |
| `profiles` | 每份配置一行，按 `position` 排序：`id`、`name`、`kind`、`url`、`path`、`auto_update_hours`、`in_pool` |
| `profile_contents` | 配置原文 `yaml`，以及下载时一起得到的信息：`fetched_at`（界面上的"更新于"）、用量四项、`proxy_count`。这是本机缓存 |
| `overrides` | `scope` 为 `global` 或某份配置的 `id`；`yaml` 是用户写的原文 |
| `settings` | 见第 8 节 |
| `state` | 本机状态（键值）：各备份目标上次收下的快照摘要 |
| `routing_state` | 只有一行：分流方式、"订阅自带"用的配置（也就是当前配置）、"我的方案"用的方案、信息节点表达式 |
| `plans`、`plan_groups`、`plan_rules` | 分流方案（routing.md 第 9 节），第 9 节 |
| `backup_targets` | 备份目标（`BackupTargets`）；云端提供方接上之前没有行 |

- 时间都用 Unix 秒。每行有 `updated_at`，只在内容确实变化时更新。`profiles`、`overrides`、`plans`、`plan_groups`、
  `plan_rules`、`backup_targets` 删除时只记 `deleted_at`（软删除），留给备份按记录合并；`profile_contents` 是缓存，
  直接删除。
- `ProfileStore` 的方法（`load`、`save`、`read_config`、`write_config`、`delete_config`、`read_override`、
  `write_override`）各是一个事务：
  - `save(list)` 写入整个列表：新配置插入，有变化的更新，列表里没有的标记删除；当前配置写入
    `routing_state.profile_id`。
  - 调用顺序是先 `write_config` 再 `save`，所以列表不会指向没有原文的配置。`Profile.updated_at`、`usage`、
    `proxy_count` 描述的是下载到的原文，因此和原文一起存在 `profile_contents`；还没有原文的配置，读出来时这三项为空。
  - `delete_config` 删除原文，并把这份配置的覆写标记删除。清空覆写文本也是标记删除，再次写入时恢复。
  - 原文不存在时报 `config_missing`；数据库读写失败时报 `storage_failed`，界面显示「无法读写本地数据：…」。
  - 如果在写入原文之后、保存列表之前崩溃，留下的原文会在下次打开数据库时清掉。
- `Profile.in_pool` 对应 `profiles.in_pool`，默认为真：这份订阅的节点进入"我的方案"的节点池（第 9 节）。
- 更新失败的标记只在内存里，不写入数据库。

### 2.2 线程

数据库只开一个连接，所有线程共用，由一把锁（`threading.RLock`）保证同一时间只有一个线程在用。

- 读写数据库的线程有三类：`ProfileLibrary` 在 asyncio 线程上用 `asyncio.to_thread` 读写，实际在默认线程池的
  多个线程里执行；设置页在 Qt 线程上写设置；启动时 Qt 线程读取配置和设置。
- 连接以 `check_same_thread=False`、`autocommit=True` 打开，事务都显式写成 `BEGIN IMMEDIATE … COMMIT`，出错时
  `ROLLBACK`。`Database.transaction()` 持锁直到事务结束，所以别的线程看不到做了一半的事务；`Database.reading()`
  只持锁，用于只读的语句。
- 没有采用每个线程一个连接，原因是：线程池里的线程数量不固定，各自的连接很难关闭；每次写入都很小（毫秒级），
  串行等待感觉不到；只有一个连接时，锁的边界就是事务的边界。
- 设置变更的通知在锁外、在写入的线程上发出（第 8 节）。
- `app.main` 先拿到单实例锁（第 8 节），再打开数据库，所以只有一个界面做迁移。退出时关闭连接；没有显式
  关闭的连接在对象回收时关闭。

### 2.3 表结构版本与迁移

- 表结构的版本记在 `PRAGMA user_version`。`database.py` 的 `MIGRATIONS` 是一串迁移函数，第 n 个把版本 n−1 升到 n。
  打开数据库时从当前版本开始依次执行，每一步和版本号的修改在同一个事务里；某一步失败，数据库停在上一个版本，
  下次启动重试。目前只有版本 1，一次建好全部表。
- 首个版本发布之前不加迁移：表结构直接改 `_SCHEMA_1`，`user_version` 保持 1（storage.md 第 2 节）。
- 文件打不开（不是数据库，或已损坏），或者版本比本程序认识的还新（降级的情况），就把它连同 `-wal`、`-shm`
  改名为 `solaris.db.<时间戳>.bad` 保留，再新建一个空库。

## 3. 订阅与切换

- **下载**在用户上下文中进行（urllib，在工作线程里执行），跟随系统代理设置。User-Agent 为
  `solaris/<版本> clash.meta`，让订阅服务返回 Clash YAML。只接受 http 和 https；超过一个请求帧能装下的
  大小（16 MiB 减 1 MiB）即拒绝。`Content-Disposition` 里的文件名作为默认名称。
- **导入**：下载或读文件 → 解码为 UTF-8 → `inspect_source`，带规则的再经宿主 `validate`（带上全局覆写）→ 写入
  存储。当前配置还空着时，第一份带规则的订阅成为当前配置（第 9 节）。
- **自动更新**：订阅默认每 12 小时更新一次，可以按配置关闭。界面每分钟检查一次到期的订阅，在启动时也检查；
  失败的订阅 30 分钟内不重试，行内显示「更新失败」。
- **切换与更新**：更新先 `validate` 再写入存储；如果更新的是当前配置且隧道在运行，随后 `reload`。切换配置时，
  隧道在运行就先 `reload`，成功后才记下新的当前配置，核心拒绝时保留原来的配置；隧道未运行只记下选择。
  下载和校验不持有锁，写入存储和重载在同一把锁下串行执行。
- UI 只把最终的 YAML 和覆写交给宿主。provider 和规则集的后续更新仍由 mihomo 自己完成。

## 4. 覆写

决策 D4：覆写只做 Merge，不做 Script。一份全局覆写作用于所有配置，每份配置还可以有自己的一份。
UI 只保存和传递覆写原文，解析与合并在内核里做；模拟宿主按同一规则实现，供预览和测试。

- 一段覆写是一个 YAML 映射；空文档等于没有覆写。`overrides` 里的各段依次应用到配置上，每段作用于上一段的结果。
- 六个插入键把列表插到目标列表的前面或后面，目标不存在时视为空列表：

  | 键 | 目标 |
  | --- | --- |
  | `prepend-rules` / `append-rules` | `rules` |
  | `prepend-proxies` / `append-proxies` | `proxies` |
  | `prepend-proxy-groups` / `append-proxy-groups` | `proxy-groups` |

- 其余键做深度合并：两边都是映射时逐键递归合并，否则用覆写的值整体替换（标量、列表和 `null` 都是如此）。
- 同一段覆写里先做深度合并，再插入列表。所以一段覆写同时写了 `rules` 和 `prepend-rules` 时，规则插在替换后的
  `rules` 前面。
- 配置准入的顺序（architecture.md §3.2）是：解析配置 → 依次应用覆写 → 平台补丁（TUN 模式、开发模式护栏）→ 安全项（architecture.md §3.3）。
  覆写在平台补丁之前，所以改不动开发模式护栏，也改不动 architecture.md §3.3 的安全项；例如覆写里的 `external-controller`
  照样会被清除。
- 某段覆写解析失败、不是映射，或插入键的值不是列表时，Validate、Start、Reload 都返回 `invalid_config`，
  `message` 是解析错误原文，界面原样显示。
- **编辑**：入口是订阅「更多」菜单里的「编辑覆写…」和订阅页底部的「全局覆写」一行；有覆写的配置在列表第二行
  写「已覆写」。保存时先用受影响的配置和新覆写调用 `validate`：配置自己的覆写用这份配置校验，全局覆写用
  当前分流方式拼出的配置校验（所以没有配置时不能编辑全局覆写）。宿主拒绝时不保存，错误显示在编辑框下方；通过后写入存储，
  隧道在运行且用的正是受影响的配置时随后 `reload`。校验、写入和重载在配置库的同一把锁下进行。

## 5. 多语言

界面提供简体中文和英文（决策 D12），用 Qt Linguist 工具链；英文文案的风格以 DESIGN.md 为准。

- **源语言是简体中文**：源码里的文案就是中文界面，`src/solaris/translations/en.ts` 是唯一的译文。
  中文界面不加载翻译，测试里的中文断言就是界面上的文字。
- **标记文案**：
  - QML 用 `qsTr()`，参数写 `%1` 再 `.arg()`；
  - ViewModel 用 `self.tr()`，上下文是类名；其他模块用 `QCoreApplication.translate("<上下文>", …)`；
  - 模块级常量（导航项、出站模式、排序方式）用 `i18n.QT_TRANSLATE_NOOP` 标记，在属性里再翻译；
  - Python 文案的参数用 `str.format` 的命名字段，如 `已运行 {uptime}`；
  - 句子写成一整条文案再填参数，不拼接翻译过的片段：如 `{origin} · {ago}更新` → `{origin} · Updated {ago}`，
    删除确认的两句话也是一条（`…删除后改用「{successor}」。{deleted}`），语序和句间空格由译文决定；
  - 模型层的错误只带英文技术细节（HTTP 状态、系统错误、mihomo 原文），由 `viewmodels/feedback.py` 按错误码
    换成译文，细节原样接在冒号后；
  - 带数量的文案写 `%n` 并传入数量，英文给单复数两种形式（`%n 个代理组` → `1 proxy group` /
    `3 proxy groups`）。中文没有复数形式，`%n` 直接换成数字。
- **日期与数字**：`models/format.py` 的日期格式本身是可翻译的 QDate 模式（中文 `M 月 d 日`，英文 `MMM d`），
  月份名由 `QLocale` 的默认值给出，所以英文显示为 Oct 20、Dec 31, 2026。相对时间（`刚刚`、`5 分钟前`）是带
  `%n` 的文案。数字、单位和速率两种语言写法相同。
- **语言选择**（`i18n.resolve_language`）按顺序取第一个有效值：
  1. 设置页的选择（设置项 `language`，见第 8 节；`system` 表示跟随系统）。`app.main(language=…)`
     和 `scripts/preview.py --lang` 在启动时优先于它；
  2. 环境变量 `SOLARIS_LANG=zh|en`，用于预览和测试，`scripts/preview.py --lang en` 等价；
  3. 系统的首选界面语言（`QLocale.system().uiLanguages()` 的第一项）：`zh` 开头用中文，其他一律用英文。

  格式化日期的 locale：系统 locale 与界面同一语言时用系统的（保留 en_GB 等地区习惯），否则用该语言的默认
  locale（zh_CN、en_US）。
- **安装**：`app.py` 在创建 ViewModel 和加载 QML 之前调用 `i18n.Translations.apply()`，安装 `QTranslator` 并
  设置 `QLocale.setDefault`。
- **运行中切换**：设置页改了语言后，`app.switch_language` 再调用一次 `apply()`，然后
  `QQmlEngine.retranslate()` 重算 QML 里的 `qsTr`，`ViewModels.retranslate()` 让每个 ViewModel 重新发出文本
  属性的通知。`pages`、`modeOptions`、`sortOptions` 和设置页的选项各有自己的通知信号，而不挂在每秒触发的
  `changed` 上，否则分段控件和菜单会每秒重建。已经显示出来的错误提示保持切换前的语言，直到下一次刷新或操作。
- **流程**：改动文案后运行 `scripts/gen_i18n.py`。`pyside6-lupdate` 扫描 `src/solaris` 下的 QML 和
  Python 并更新 `.ts`（不记录行号，移动代码不会改动目录），`pyside6-lrelease` 再编译出 `.qm`。新条目在 `.ts`
  里翻译（手改或用 `pyside6-linguist`），然后再运行一次。`--check` 和 `tests/test_i18n.py` 检查：`.ts`
  覆盖当前源码；英文没有未完成的条目，占位符与源文一致；`.qm` 与 `.ts` 一致。
- **`.qm` 提交进仓库**（与 Theme.qml 一样），从源码运行和预览不需要额外步骤。Nuitka 打包时，
  `package/entry.py` 里的 `nuitka-project` 选项把 `translations/*.qm` 复制到 `solaris/translations/`，
  `i18n.py` 按包内路径加载；找不到 `.qm` 时启动报错，而不是静默显示中文。
- **Qt 自带的文案**：输入框和编辑框的右键菜单（撤销、重做、剪切、复制、粘贴、删除、全选）来自 Qt 自己的 QML，
  `QtQuick.Controls.impl` 的 `UndoAction`、`CutAction` 等文件里的 `qsTr`，所以翻译上下文就是这些文件名。Qt 附带的
  zh_CN 目录（PySide6 6.12）还没有这些条目，本项目在 `translations/qtquick_zh_CN.ts` 里补上中文，中文界面下由
  `Translations` 一并加载（`i18n.QT_CATALOGS`）；英文界面就是 Qt 的原文，不需要目录。
  - 这个目录不在 `gen_i18n.py` 的流程里（lupdate 扫不到 Qt 的源码），改动后用
    `pyside6-lrelease -nounfinished -silent src/solaris/translations/qtquick_zh_CN.ts -qm src/solaris/translations/qtquick_zh_CN.qm`
    编译；`.qm` 同样提交进仓库，打包时随 `translations/*.qm` 一起复制。
  - `tests/test_qt_translations.py` 核对：每个条目都是 Qt 那个文件里的 `qsTr` 原文，Basic 样式的菜单正好用这几个
    Action（升级 Qt 后有变化就失败）；`.qm` 与 `.ts` 一致；在中文和英文界面下实际创建这些 Action，读出的文字分别是
    中文和 Qt 的英文原文。
  - 其他 Qt 自带的文案（如系统文件对话框）由操作系统提供，跟随系统语言。

## 6. 外观

界面有浅色和深色两套外观（决策 D9），取值和规则见 DESIGN.md「Colors → 深色」。

- **主题**：DESIGN.md 的 `colors` 是浅色值，`colors.dark` 是同名角色的深色值。`scripts/gen_theme.py` 把每个
  角色生成为一个绑定，如 `primary: dark ? "#6495FF" : "#1F5EFF"`；`Theme.dark` 取自注入的外观对象。外观变化时
  所有绑定随之重算，界面立即重绘，不需要重启或重新加载 QML。
- **外观对象** `viewmodels/appearance.py` 的 `Appearance`，在 QML 里是 `Solaris` 模块的类型：

  | 成员 | 说明 |
  | --- | --- |
  | `mode`（属性，`modeChanged`） | `"system"`、`"light"` 或 `"dark"` |
  | `dark`（属性，`darkChanged`） | 界面实际使用的外观，只读 |
  | `setMode(mode)`（槽） | 改变 `mode`；其他值抛出 `ValueError` |

  `system` 模式下 `dark` 跟随 `QGuiApplication.styleHints().colorScheme()`，并监听 `colorSchemeChanged`，系统在
  运行中切换外观时立即更新；系统没有报告配色方案时（如 offscreen 平台）按浅色处理。固定为浅色或深色时，
  `Appearance` 同时调用 `QStyleHints.setColorScheme()` 让原生标题栏一致；回到 `system` 时调用
  `unsetColorScheme()`。这只影响本应用，不改系统设置。
- **注入**：`viewmodels/composition.compose(…, appearance=…)` 创建或接收外观对象，放进 `ViewModels.appearance`，
  作为 Main.qml 的必需属性 `appearance` 注入；Main.qml 用 `Binding` 把它交给 `Theme.appearance`。
  设置页只改设置项 `appearance`（第 8 节）；`compose()` 订阅设置存储，值变化时调用外观对象的 `setMode()`，
  所以无论改动来自设置页还是备份恢复，外观都立即跟上。
- **启动时的模式**（`resolve_mode`）按顺序取第一个有效值：
  1. `app.main(appearance=…)` 的参数，然后是设置项 `appearance`（`system` 视为未指定）；
  2. 环境变量 `SOLARIS_APPEARANCE=system|light|dark`，用于预览和测试，
     `scripts/preview.py --appearance dark` 等价；
  3. `system`。
- **Qt 控件的调色板**：Basic 样式里本项目没有重绘的部分（滚动条、Qt 自带的右键菜单和工具提示、文字选区的
  默认值）读窗口的 `palette`，Main.qml 把它映射到 token，浮层继承窗口的调色板：

  | palette | token |
  | --- | --- |
  | `window` | `background` |
  | `windowText`、`text`、`buttonText`、`toolTipText` | `text` |
  | `base`、`button`、`toolTipBase` | `surface` |
  | `alternateBase` | `surface-subtle` |
  | `light`（菜单项高亮） | `surface-selected` |
  | `midlight` | `border` |
  | `mid`（滚动条滑块、Qt 自带菜单的分隔线） | `border-control` |
  | `dark`（按下的滑块、Qt 自带菜单的外框） | `text-muted` |
  | `placeholderText` | `text-muted` |
  | `highlight`、`accent` | `primary` |
  | `highlightedText`、`brightText` | `on-primary` |
  | `link`、`linkVisited` | `link` |
  | `shadow` | `scrim` |

- **字体**：Inter（400、500、600）和 JetBrains Mono（400）放在 `ui/fonts/`，各带 `OFL.txt`，由 `Fonts.qml` 的
  `FontLoader` 加载；Nuitka 打包时 `package/entry.py` 把整个目录复制到 `solaris/ui/fonts/`。中日韩文字
  由 Qt 从系统字体回退。SIL OFL 1.1 允许字体与任何软件捆绑分发，只要求随附版权声明和许可证、不单独出售
  字体、修改版不用保留字体名；字体不受 GPL 约束，也不改变本项目的 GPL-3.0 许可。
- **测试**：`tests/test_theme.py` 检查 Theme.qml 与 DESIGN.md 一致、两组颜色角色相同，并按 DESIGN.md 的用法
  核对两种外观的对比度；`tests/test_appearance.py` 用 `tests/support/appearance.py` 模拟系统切换外观；
  `tests/test_app.py` 在浅色和深色下加载界面，并检查系统切换后窗口立即重绘。

## 7. 连接与日志

连接页和日志页建立在 architecture.md §4.4 的订阅上，宿主接口里对应这几个方法：

| 方法 | 协议 | 说明 |
| --- | --- | --- |
| `poll_connections()` | `SubscribeConnections` + `PollEvents` | 返回最新一份快照。`ServiceHost` 缓存它：两次拉取之间内核还没采新快照时，返回同一份 |
| `close_connection(id)` | `CloseConnection` | 内核已不再跟踪的 id（`invalid_request`）视为已关闭，不报错 |
| `close_all_connections()` | `CloseAllConnections` | 未运行时为 `not_running` |
| `poll_logs(level)` | `SubscribeLogs` + `PollEvents` | 上次拉取以来、不低于 `level` 的日志行。换级别时先退订旧订阅，再按新级别订阅 |

- **共用订阅**：概览页和连接页都调用 `poll_connections()`，用的是 `ServiceHost` 里同一个连接订阅。快照每份都
  取代前一份，所以缓存最新一份就够了；不缓存的话，两个页面会互相取走对方的快照。
- **链路顺序**：mihomo 从最终节点往外记录链路（`["香港 01", "节点选择"]`），`ServiceHost` 把它反转成
  `Connection.chains` 约定的"最外层的组在前"。
- **`Connection`** 另带 `source`、`destination`（`ip:端口`）。`host` 是域名，不带端口；直连 IP 的连接没有
  `host`，表格显示目标地址。

**连接页**（`models/connections.py`、`viewmodels/connections.py`）：

- 只在页面显示时每秒拉取；离开页面停止拉取，并丢弃速率样本。
- 内核只给每条连接的累计字节数，速率是最近 4 次拉取里累计值的变化除以经过的时间。取 4 次的平均，一是让排序
  不随每秒的抖动跳动，二是抹平"两次拉到同一份快照"带来的 0 和翻倍。
- 搜索不区分大小写，匹配主机、目标地址、进程、规则和链路；网络筛选为全部 / TCP / UDP。排序有速率、流量、
  时长三种，默认按时长从长到短（即最早建立的在前，新连接追加在末尾）；再点同一列反向。值相同的行按建立时间
  保持原有次序，避免每秒互换位置。
- 表格的数据源是 `viewmodels/common/keyed_list.py` 的 `KeyedListModel`：按连接 id 把新列表同步进模型，依次发出
  删除、一次重排（`VerticalSortHint`，QML 只重新绑定委托，不重建）、插入，最后一次 `dataChanged`。2000 条连接
  时一次刷新在 GUI 线程上约 30 ms。
- 选中的连接由 ViewModel 记住（id），排序或增删行后仍指向同一条；它结束后详情保留最后的数值并标明已关闭。
- 单个连接直接关闭；"全部关闭"先弹破坏性确认框。

**日志页**（`models/logs.py`、`viewmodels/logs.py`）：

- 应用启动后就开始收集，与当前显示哪一页无关，打开日志页时能看到之前发生的事。每 0.5 秒拉取一次，界面最多
  保留最近 5000 条，更早的丢弃。
- **级别**分两层：
  - 页面上的级别是显示过滤：只显示不低于所选级别的行。调高或调低都立即作用于已经收到的行，不会丢失什么。
  - 订阅级别跟着它走：选"调试"时按 debug 订阅，选其他级别时一律按 info 订阅。调试日志量大，常开会把 info
    行挤出缓冲区。所以切到"调试"后只能看到此后的调试日志；切回其他级别时重新按 info 订阅。
  - mihomo 配置里的 `log-level` 不影响订阅：mihomo 把每一行都发给订阅者，`log-level` 只决定它自己打印到标准
    输出（Windows 上即服务日志）的内容。界面不改 `log-level`。
- 暂停只冻结显示：新行照常收进缓冲区，标题栏状态写明暂停期间新增了多少条，继续后一并显示。清空会清掉
  缓冲区。
- 搜索不区分大小写，匹配消息原文。导出写出当前显示的行（即经过级别和搜索过滤后的），UTF-8，每行带日期和
  毫秒：`2026-10-29 12:04:01.123 INFO  [TCP] …`。

## 8. 设置与桌面行为

用户的设置由 `models/settings.py` 的 `SettingsStore` 接口提供：读取 `current`、写入
`update(**changes)`、订阅变更 `subscribe(listener)`（回调收到改动前后的 `Settings`，在写入的线程上调用，
返回取消订阅的函数），以及别的代码直接写了表之后的 `reload()`。设置页（`viewmodels/settings.py`）、
「接管方式」的 TUN 与系统代理开关、托盘和关闭窗口的行为都只依赖这个接口，并且通过订阅跟上变化，不论改动来自
哪一页，还是从备份恢复。不接受的值抛
`ValueError`，写不进去抛 `SettingsError`（`settings_unwritable`），这两种情况都不通知，设置页标题栏显示
「无法保存设置：…」。

**存储**：`DatabaseSettingsStore` 把设置存在数据库的 `settings` 表里（第 2 节）。每个改过的设置项占一行：`key` 是
字段名，`value` 是 JSON，`scope` 是这一项的范围，`updated_at` 是修改时间。没有对应行或值不被接受的设置项取默认值。
一次 `update` 只写有变化的项，在一个事务里完成，失败时一项都不写。应用里每个数据库只有一个设置存储；直接写
`settings` 表的代码（恢复备份）写完后调用 `reload()`，订阅者同样会收到改动前后的 `Settings`。

每个设置项在字段元数据里标明范围（`settings.scope(name)`），供设计备份范围：`synced` 随账户同步，换一台设备
恢复后照样适用；`local` 只在本机生效，恢复到别的设备上没有意义或有风险。

| 设置项 | 取值 | 默认 | 范围 | 含义 |
| --- | --- | --- | --- | --- |
| `language` | `system`、`zh`、`en` | `system` | 同步 | 界面语言（第 5 节） |
| `appearance` | `system`、`light`、`dark` | `system` | 同步 | 外观；主题按它取色 |
| `close_action` | `tray`、`quit` | `tray` | 本机 | 关闭主窗口时留在托盘，还是退出界面；隧道都不受影响 |
| `launch_at_login` | 布尔 | `false` | 本机 | 登录时打开界面。以系统里的登录项为准，界面每次读到系统的状态就写回这里 |
| `tun` | 布尔 | `true` | 本机 | 设置页「接管方式」的 TUN 开关：连接时是否开启 TUN。macOS 上总是开启，不看这一项 |
| `system_proxy` | 布尔 | `true` | 本机 | 设置页「接管方式」的系统代理开关：连接时是否设置系统代理。只有 Windows 服务设置系统代理 |
| `mixed_port` | 1024–65535 | 7890 | 本机 | 混合端口，总是覆盖配置里的 `mixed-port` |
| `allow_lan` | 布尔 | `false` | 本机 | 允许局域网连接（architecture.md §8"局域网暴露"） |
| `tun_stack` | `profile`、`system`、`mips`、`gvisor`、`mixed` | `profile` | 本机 | "TUN 协议栈"（D3）：`profile` 是"跟随配置"，用配置里的 `tun.stack`；其余写进 `RunOptions.tun_stack`，盖过配置和覆写（architecture.md §3.1、§3.4）。各平台的默认值等实测后再定 |
| `include_all_networks` | 布尔 | `false` | 本机 | 仅 macOS（D19）："包含所有网络"，把系统默认绕开隧道的流量也送进隧道，局域网流量除外。写进 VPN 配置的 `includeAllNetworks` 和 `excludeLocalNetworks`，下次连接时生效；打开后 TUN 协议栈不能用 `system` 和 `mixed`（architecture.md §3.4） |
| `warp_protocol` | `masque-h2`、`masque-h3`、`wireguard` | `masque-h2` | 本机 | WARP 出口的协议，在分流页的出口设置对话框里改，设置页不列出。账户只有一种协议的密钥，改了协议就为它换密钥（exits.md 3.1、3.2 节） |
| `update_check` | 布尔 | `true` | 同步 | 自动检查更新：启动 30 秒后检查一次，之后按 `update_check_hours` 定期检查（release.md 第 2 节） |
| `update_check_hours` | `24`、`72`、`168` | `24` | 同步 | 自动检查的间隔：每天、每 3 天、每周 |

- **映射到 `RunOptions`**（第 1 节）：Start 用 `Settings.run_options()` 的各项（`tun_stack` 为 `profile` 时传空串）；
  Reload 的 TUN 和系统代理取自 `GetStatus`，其余取自设置。
  - **连接开关**（D26，DESIGN.md「连接开关」）：概览页「出站」分组第一行的「连接」是界面上唯一的连接控件，托盘
    菜单的「连接 / 断开」照旧。打开即按设置连接，关闭即 `Stop`。进行中开关不可操作，第二行写「正在连接…」
    「正在断开…」，宿主报告 `starting`、`stopping` 时也一样；操作完成后等下一次刷新读到隧道的新状态才结束，
    开关不会先跳回原位。连不上时开关回到关闭，第二行用 `danger` 写原因，留到下一次操作。已连接时概览页标题栏
    写接管方式和运行时长：「TUN 与系统代理 · 已运行 2:14:36」，只开一项时写「TUN」或「系统代理」，两项都没开时
    写「混合端口 7890」。
  - **接管方式**：连接时能接管什么由宿主决定（`TunnelHost.takeover`）。宿主发出的每份 `RunOptions` 都先经
    `taken_over` 改写，不论设置怎么写：

    | 宿主 | `takeover` | 设置页「接管方式」 | 连接时 |
    | --- | --- | --- | --- |
    | Windows 服务 | `settings` | 「TUN 模式」「系统代理」两个开关，默认都开 | 按开关 |
    | macOS 扩展 | `tun` | 「TUN 模式」一行右侧写「已启用」，第二行「macOS 上总是启用」；没有系统代理一行 | 总是 TUN，不设系统代理 |
    | 开发服务 | `none` | 两行右侧都写「不使用」，分组标题右侧写「开发模式只开放混合端口」 | 只开放混合端口 |

    分组标题右侧平时写「连接时使用」。两个开关由 `OverviewViewModel` 提供，因为要显示隧道的状态：隧道运行时
    显示内核报告的状态，拨动先改变隧道（TUN 是带新 `RunOptions` 的 Reload，系统代理是 `SetSystemProxy`），
    宿主接受后才记入设置，所以已连接时改这两项立即生效；隧道停止时显示设置里的选择，拨动只改设置。
  - **两项都关闭**（Windows）：照常连接，内核只开放混合端口（不允许局域网连接时只在回环地址上），系统的设置
    一项不改，要走代理的程序自己指向这个端口。系统代理一行第二行写「两项都关闭时，连接后只开放混合端口」。
  - **macOS 不设系统代理**：扩展不提供 `SetSystemProxy`（architecture.md §6.3），TUN 已经接管全部流量，所以
    不列这一行，`system_proxy` 在 macOS 上不起作用。
  - **端口、局域网连接与 TUN 协议栈**：隧道运行时先带新值 Reload，成功后才写入设置；内核拒绝（如端口被占用，
    `apply_failed`；内核不带 `with_gvisor` 时选了 gVisor 或 Mixed，`invalid_config`）时保留原值，错误写在对应的
    行下。隧道停止时直接写入。
  - **TUN 协议栈**在"接管方式"分组里 TUN 模式的下一行，是下拉框：跟随配置、System、Mips、gVisor、Mixed，栈名用
    mihomo 的写法，不翻译。
  - **包含所有网络**是它下面的开关，只在 macOS 上显示，说明写着「系统默认绕开隧道的流量也进入隧道，下次连接时
    生效」。打开它只写设置，不动运行中的隧道；打开时 TUN 协议栈若是 System 或 Mixed，就和设置一起改成 gVisor，
    这一步像改栈一样先带新值 Reload。打开期间下拉框里的 System、Mixed 不可选，注记「包含所有网络时不可用」。
  - **开发模式**：开发服务对开启 TUN 或系统代理返回 `unsupported_method`（architecture.md §11）。Windows 以外的
    服务都是开发服务，`app.default_host` 给它的 `ServiceHost` 是 `TAKEOVER_NONE`，界面不向它要这两项，所以两项
    默认打开时开发模式照样能连接，只开放混合端口。直接向开发服务要求时，`ProfileLibrary` 仍把拒绝换成
    `tun_unsupported`、`system_proxy_unsupported` 或两者都有的 `tun_and_system_proxy_unsupported`，界面显示
    「开发模式不支持 TUN 模式」等提示，设置不改。
- **外观**：设置页只负责选择和保存（`setAppearance` 写设置项 `appearance`），主题跟随第 6 节的外观对象。
- **托盘**用 `Qt.labs.platform` 的 `SystemTrayIcon`（`ui/components/TrayIcon.qml`）。它在 macOS 和 Windows 上
  有 Qt 平台插件的原生实现，`QGuiApplication` 就够，不必为 `QSystemTrayIcon` 引入 Qt Widgets 和
  `QApplication`。没有原生托盘的平台（Linux、测试用的 offscreen）上 Labs Platform 会要求 Qt Widgets，所以
  只在 `cocoa` 和 `windows` 上创建托盘，其余平台关闭窗口即退出界面。
  - 菜单：连接状态（不可点）、连接或断开、出站模式子菜单、打开主窗口、「退出界面（隧道保持运行）」。
    这是系统原生菜单，外观跟随平台，不按 DESIGN.md 的窗口内菜单绘制。
  - 图标在 `ui/tray/`，是侧栏标记的线条图形，未连接时调成 45% 不透明度。macOS 用模板图（只看 alpha，由菜单栏
    着色）；Windows 按任务栏主题（注册表 `SystemUsesLightTheme`，Qt 报告的是应用主题，不是任务栏的）选深色或
    白色图形，每 10 秒和系统配色变化时重读。
- **关闭窗口**：`tray`（默认）时隐藏窗口，界面留在托盘；Windows 上点托盘图标、macOS 上点 Dock 图标（Qt 把每次
  点击报告为应用激活）恢复窗口。`quit` 时退出界面进程。两种情况下隧道都继续运行（architecture.md §1）。
- **单实例**：第一个界面在用户数据目录持有 `ui.lock`（`QLockFile`，进程不在了的锁视为过期），并以
  `solaris-<用户名和数据目录的哈希>` 为名监听 `QLocalServer`（只允许同一用户连接）。后来的界面拿不到
  锁，就连上这个名字发一行 `activate`，然后退出；第一个界面收到后显示并激活窗口。后来的界面是带着一键导入
  链接启动的（Windows 上链接是命令行参数），就改发 `open <链接>`，第一个界面显示窗口并预填导入表单
  （release.md 第 7 节）；一行超过 16 KiB 的消息被丢弃。Windows 上后来的界面先调用
  `AllowSetForegroundWindow`，让第一个界面能把窗口带到前台。由锁而不是由服务端名字来判断，是因为 macOS 上
  崩溃的界面会留下 socket 文件，而且两个同时启动的界面拿锁时只有一个能成功。
- **备份与同步**：设置页左栏的一个分组，由 `viewmodels/backup.py` 的 `BackupViewModel` 提供（storage.md 第 3、6 节）。
  iCloud、Google Drive 两行在它们的提供方实现之前不显示，目前有两行：
  - 「导出到文件」：说明写着「不加密，其中有订阅链接」；「导出…」先弹确认框「备份文件不加密，含订阅链接和配置原文，请妥善保管。」
    （本地文件配置的原文里可能有组网出站的凭据，mesh.md 第 7 节），确认后在系统的保存对话框里选位置，默认文件名
    `solaris-backup-<时间>.json`。导出后说明处显示「已导出到 …」或失败原因，6 秒后恢复原来的说明。
  - 「从文件恢复」：「恢复…」打开系统的打开对话框。本机没有配置时直接按"替换"导入；有配置时弹出「从文件恢复」
    对话框：备份的设备、时间和配置、方案的份数，本机现有的份数，以及"合并"（默认）和"替换"两个选项，「恢复」是
    默认按钮。恢复期间两行的按钮不可用，说明处显示「正在恢复，并重新下载订阅…」；结束后显示「已从 … 恢复」，
    有配置更新失败时显示「已恢复；N 份订阅更新失败，已在订阅页标出」，隧道没能切到恢复后的分流时说明原因。这一句
    一直留到下一次恢复。不是备份文件、版本更新的备份、读不了的文件都在这里说明，数据不动。
  - 恢复后 `BackupViewModel.restored` 通知订阅页、分流页、概览页和代理页重新读取，设置存储 `reload()`，语言和外观
    随之切换。
- **关于**：设置页的「关于」分组除了版本，还有检查更新与下载、自动检查的两个设置项、开源许可和诊断信息导出，
  由 `viewmodels/about.py` 的 `AboutViewModel` 提供，设计见 [release.md](release.md)。
- **登录时打开**（`desktop/login.py`，平台接口 `LoginItem`）：
  - Windows：为当前用户注册一个计划任务（`schtasks /Create /XML`）：登录触发器只针对该用户，以该用户身份、
    交互式令牌、不提权运行，普通优先级，没有运行时限，动作是 `<界面>.exe --background`（界面只进托盘）。
    任务名 `Solaris (<域>_<用户名>)`。用计划任务而不是注册表的 `Run` 键：登录后立即启动，不受 Windows
    对启动项的延迟；卸载器按同一个任务名清理（`package/windows/setup.ps1`）。读状态用
    `schtasks /Query /XML`，用户在任务计划程序里禁用的任务视为关闭。
  - macOS：`SMAppService.mainAppService` 的 `register` / `unregister`（macOS 13 起），经 ctypes 调用
    Objective-C 运行时，不依赖 PyObjC。状态为 `requiresApproval` 时设置页提示到系统设置的登录项里允许，并给出
    「打开系统设置」链接。登录项启动的界面目前会显示窗口。
  - 只有打包后的程序（Nuitka 编译，macOS 上还要在 `.app` 里）才注册；从源码运行时是 `UnavailableLoginItem`，
    开关不可用并注明「仅安装版可用」，避免在开发机上把 Python 解释器注册成登录项。测试只用替身，真实注册在
    M4、M5 的测试机上验证。

## 9. 分流

设计见 [routing.md](routing.md)；这里写界面怎样实现它（roadmap C1、M8b）。

- **分流方式决定隧道运行什么**（`ProfileLibrary`）：
  - "订阅自带"（`routing_state.mode = 'profile'`）：当前配置（`ProfileList.active`，即 `routing_state.profile_id`）的
    原文，覆写为全局覆写加这份订阅自己的（routing.md §5.6）；
  - "我的方案"（`'plan'`）：`TunnelConfig.composition`，节点池里各订阅按列表顺序的原文、正在使用的方案
    （`plans.plan_json`，`locale` 跟随界面语言）和信息节点表达式（`routing_state.info_pattern`，为 NULL 时用
    `DEFAULT_INFO_PATTERN`）；覆写只有全局覆写。
  - Start、Reload、改 TUN 和端口时的重载都按当前的分流方式拼出配置。
- **导入与更新**：先 `inspect_source`。订阅带规则时再用自己的原文 `validate`（它要能单独使用）；只有节点的订阅
  （包括分享链接）不单独校验。分流方式是"我的方案"且这份订阅加入节点池时，还要用加上它之后的拼装校验。
  `proxy_count` 数不出来时（分享链接）取 `inspect_source` 的节点数。读出来的 `SourceInspection` 缓存在内存里，
  界面启动后对还没读过的订阅补读一次（`inspect_all`），用于"只有节点"的标注和挑选接替的订阅。
- **默认值**（routing.md §5.1）：
  - `mode` 为 NULL 只表示还没导入过订阅。第一次导入时，带规则就是"订阅自带"并使用这份订阅；只有节点就是
    "我的方案"，按模板生成方案（`plans.default_plan`，名为"日常"）。之后的导入不改分流方式；当前配置还空着时，
    第一份带规则的订阅成为当前配置。
  - 删除规则正在使用的订阅：改用列表里最早的另一份带规则的订阅，一份都没有就切到"我的方案"（没有方案时按
    模板生成）。隧道在运行时先用接替者重载，重载失败就不删除。
- **生效**（routing.md §5.4）：方案的每次改动都是"先 `validate`、再存储、正在使用时 `reload`"，在配置库的同一把
  锁下进行。改动的结果是 `CompositionReport`：警告按条目 id 显示在规则行上，地区组和节点数供目标下拉框、策略组
  标签和代理页使用。报告随节点池、方案和信息节点表达式的变化失效，分流页显示时重新拼装一次。
- **`RoutingViewModel`**（`viewmodels/routing.py`）：
  - 改动立即显示，再交给配置库；被拒绝时恢复为已存储的方案，原因写在标题栏。
  - 拖动和菜单里的上移、下移先只改显示，停止 1 秒后作为一次改动提交；其他改动会顺带提交它们。
  - 删除规则后标题栏显示撤销提示 8 秒，或者到下一次改动为止；撤销就是把删除前的方案存回去（软删除的记录恢复）。
  - 添加规则对话框是 `RuleEditor`（`viewmodels/rule_editor.py`）：服务、分类、域名和 IP 段四种；多选的服务
    和分类各生成一条，域名和 IP 段一次生成一条；新条目插在最前面，编辑保持位置和 id。内核拒绝时对话框不关闭。
    分类列表取自 `list_geo_categories`，连不上时用安装包附带的清单。
  - "订阅自带"且这份订阅正在运行时，用 `list_groups` 和 `list_rules` 只读列出它的策略组和规则；不在运行时
    写出 `inspect_source` 给的数量。
  - **规则测试**（M8b）：测试框的文字由 `plans.match_target` 读成主机和端口，停顿 0.4 秒或回车后交给
    `ProfileLibrary.match_rule`：用显示的方案（含未提交的移动）、节点池和全局覆写拼成 `TunnelConfig`，调用宿主的
    `match_rule`。结果的条目 id 标出命中的行（`RuleRow.hit`，兜底是 `fallbackHit`），方案每次改动生效后用同一段
    文字重测。
  - **搜索与按目标筛选**只影响显示：`RuleRow` 仍按完整列表算 `first` / `last`，所以菜单里的上移、下移照常；
    `filtering` 为真时 QML 禁用拖动手柄。
  - **自建策略组**：`customGroups` 列出方案里的自建组，说明写"类型 · 节点数、其他成员数、直连 / 拒绝"，节点数取
    报告里的 `node_count`。编辑器是 `GroupEditor`（`viewmodels/routing_groups.py`）：条件改动停止 0.25 秒后经
    `ProfileLibrary.preview_group` 调用 `PreviewGroup`（组名临时用 id，名称不影响预览），延迟取自运行中内核的
    `list_groups`。保存走 `RoutingViewModel.put_group`：编辑的组保持位置，新组排在最后，内核拒绝时对话框不关闭。
    删除前列出引用它的规则条目、兜底和其他组（routing.md §5.5），确认后这些规则改走节点选择、其他组去掉它，
    可以撤销。
  - **转换与导出**是 `PlanTransfer`（`viewmodels/routing_transfer.py`）：`convert_profile` / `convert_file`
    调用 `ConvertToPlan`，摘要对话框确认后用 `plans.plan_from_json` 把内核写的方案 JSON 读成 `Plan`（条目的
    `items` 还原成界面的 `content`：域名加 `=`、`~` 前缀，IP 段带 `no_resolve`，分类的 GEOIP 没有 `no-resolve`
    时记 `"no_resolve": false`），再由 `ProfileLibrary.add_plan` 校验、存储，勾选"创建后改用"时切到"我的方案"
    并使用它。导出和"查看生成的配置"调用 `export_plan`（`EXPORT_SHARE` / `EXPORT_GENERATED`，后者带运行中的或
    设置里的 `RunOptions`），只读显示；复制进剪贴板，另存为写 UTF-8 文件。
- **目标下拉框**（规则对话框和"其他流量"，`routing_text.target_options`）：节点选择、自动选择 → 有节点的地区 →
  自建组 → 组网出站 → WARP 出口 → 直连、拒绝，段之间有分隔线，空段省略。方案没有打开 WARP 出口时，这一项
  禁用并注明"未启用"。规则行按报告的警告写目标后的提示：`region_empty`"无节点，暂走节点选择"、`group_empty`
  "没有成员，暂走节点选择"、`egress_unavailable`"不可用，已拒绝连接"、`mesh_missing`"不存在，已拒绝连接"；
  `via_empty`、`mesh_restricted` 不属于哪条规则，写在出口行和组网行上。
- **组网出站**（mesh.md 第 5 节）：策略组标签里自建之后是"组网"一节，行来自报告的 `mesh`（只在"我的方案"下），
  说明写"类型 · 来自「订阅名」"，有 `mesh_restricted` 时加"· 已关闭 选项"；节点池里没有组网出站时整节不显示。
  名称取报告里生成配置用的名称，报告里没有了（`mesh_missing`）时取 `mesh:` 引用里的原名。"更多"菜单：
  - "添加常用规则"：Tailscale 把 `plans.common_mesh_rules` 的 IP 段规则（`100.64.0.0/10`、`fd7a:115c:a1e0::/48`，
    `no-resolve`）和域名规则（`ts.net`）插在最前面，已有一样的条目就跳过，标题栏给出撤销提示；ZeroTier、EasyTier
    打开添加规则对话框的"IP 段"，目标已选好（`RuleEditor.openAddRanges`）。
  - "使用它的规则"：把目标筛选设为这个出站，切到规则标签。
- **链式出口**（exits.md 第 6 节）：组网之后是"链式出口"一节，目前只有 WARP 一行，由 `WarpExit`
  （`viewmodels/routing_exits.py`）提供。说明写"经由 组名 · 协议"，经由组没有成员（`via_empty`）时写"组名，暂经
  节点选择"；右侧是状态："未启用"、"正在注册…"、"已就绪"、"未注册"、"连接后注册"，注册失败时为 `warning` 色的
  "不可用"（原因在对话框的账户一栏）。点行或菜单的"设置…"打开出口设置对话框（`WarpDialog.qml`）：
  - 开关、经由（策略组下拉框）、协议（三选一）都立即生效，没有保存按钮（DESIGN.md"开关"）：开关和经由改的是
    方案（`plans.egress`，与其他改动一样先校验再存储），协议改的是本机设置 `warp_protocol`。
  - 打开开关时还没有账户就注册；有账户时改协议就为它换密钥。"注册""重新注册"按钮在账户一栏，重新注册是换一个
    新设备。删除账户在账户一栏的"更多"菜单里，先确认，只删本机保存的副本。
  - 注册经 `ProfileLibrary.register_warp`：经由组按报告里的组名传给宿主（没有成员时传节点选择），网络请求不占
    配置库的锁；账户存进凭据库后，在用的方案重新拼装并重载。隧道没在运行（或在用"订阅自带"）时记下这次请求，
    状态写"连接后注册"；概览页看到隧道开始运行时（`RoutingViewModel.tunnel_running`）补做，协议在断开时改过的也在
    这时换密钥。这个请求只在界面进程的内存里，退出界面就不再等。
  - 账户是凭据，存系统凭据库（`models/credentials.py`，D14）：`KeyringCredentials` 经 `keyring` 直接用平台的后端
    （macOS 钥匙串、Windows 凭据管理器，不靠入口点发现），`WarpAccounts` 把账户写成一条紧凑的 JSON（远小于
    Windows 一条凭据 2560 字节的上限）。它不进数据库，也不进备份；读不出来（如钥匙串锁着）时按没有账户处理，
    WARP 的规则拒绝连接。`ProfileLibrary` 拼装时只给打开了 WARP 出口的方案带上账户（`Composition.egress`）。
    测试把 `credentials.system_keyring` 换成内存里的实现（`tests/conftest.py`），不碰真实的凭据库。
- **其他页面**：订阅页的行在说明里写"规则使用中""不加入节点池"；概览页"出站"分组的"当前使用"跳到分流页；
  代理页用报告里的节点池标出每个节点的来源订阅，地区组收进可折叠的"地区"小节；连接页用
  `Connection.rule_entry_id` 显示规则条目名称，第二行是内核的原始规则。
- **服务清单与地区名称**：`models/services.py`（41 个服务，九个类别）和 `models/regions.py`（与内核的地区表
  同序，名称用 `qsTr` 的英文译文）。服务的中英文名称在清单里，按当前安装的翻译选用（`i18n.current_language`）。
  `tests/test_services.py` 核对清单引用的分类都在附带的 GEO 数据里，地区表与 `regions.go` 一致。

