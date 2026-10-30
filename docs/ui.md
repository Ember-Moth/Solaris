# 界面

本文描述 Python 界面的结构和各部分的设计：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为。
系统整体的架构见 [architecture.md](architecture.md)，视觉规范见 [DESIGN.md](../DESIGN.md)。云备份的设计见
[storage.md](storage.md)，自定义分流的设计见 [routing.md](routing.md)，两者的界面部分尚未实现。

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
  - `MacExtensionHost`（未实现）：启停和控制消息都通过 ctypes 调用管理库。

  测试和 `scripts/preview.py` 用 `tests/support/simulated.py` 里的模拟宿主。

## 1. 宿主接口里的配置

`validate`、`start`、`reload` 都接收 `TunnelConfig`，对应协议里的 `ValidateRequest`、`StartRequest`、
`ReloadRequest`：

| 字段 | 内容 |
| --- | --- |
| `config_yaml` | 配置原文，即 `ProfileStore` 里存的 YAML |
| `overrides` | 覆写的 YAML 原文，按应用顺序排列：先全局覆写，再这份配置自己的覆写；没有设置的不放进来。语义见第 4 节 |
| `options` | `RunOptions`：`tun_enabled`、`system_proxy_enabled`、`mixed_port`（0 表示沿用配置）、`allow_lan`。`validate` 不使用 |

- **Start** 的四项都取自设置（第 8 节）：TUN 和系统代理是用户在开关上的选择，端口和局域网连接是设置页的值。
- **Reload** 用 `GetStatus` 报告的 TUN 和系统代理状态，所以切换配置或订阅更新都不会改变隧道的这两项；端口和
  局域网连接取自设置。
- 错误码：配置被拒绝时为 `invalid_config`（`message` 是 mihomo 的原始错误，界面显示在「配置无效：」之后）；
  未运行时 Reload 返回 `not_running`；运行中 Start 返回 `already_running`；端口打不开（例如混合端口被其他程序
  占用）时为 `apply_failed`，`message` 是系统的监听错误，界面显示在「配置未能生效：」之后，Reload 失败时隧道
  保持原来的配置和端口，Start 失败时状态为"连接失败"。中文提示在 `viewmodels/feedback.py`。
- 只有 DIRECT、没有任何节点的配置是合法的；策略组或规则引用了不存在的节点或策略组时，内核拒绝。

## 2. 配置存储

界面的本地数据（配置、覆写、设置、分流方案）存在用户数据目录下的 SQLite 数据库 `mihomo-desktop.db` 里。用户数据
目录在 Windows 上是 `%LOCALAPPDATA%\<Product>\`，在 macOS 上是 `~/Library/Application Support/<Product>/`，
`<Product>` 取 `product.py` 的 `DISPLAY_NAME`。数据库的整体设计和云备份见 [storage.md](storage.md)。代码分布：

- `models/database.py`：连接、表结构、迁移；
- `models/store.py`：配置和覆写（`ProfileStore`），以及打开数据库的 `open_database`；
- `models/settings.py`：设置（第 8 节）；
- `models/routing.py`：分流数据，界面在 C1 实现；
- `models/file_import.py`：读取旧版本的文件存储（2.4 节）。

### 2.1 表

| 表 | 内容 |
| --- | --- |
| `profiles` | 每份配置一行，按 `position` 排序：`id`、`name`、`kind`、`url`、`path`、`auto_update_hours`、`in_pool` |
| `profile_contents` | 配置原文 `yaml`，以及下载时一起得到的信息：`fetched_at`（界面上的"更新于"）、用量四项、`proxy_count`。这是本机缓存 |
| `overrides` | `scope` 为 `global` 或某份配置的 `id`；`yaml` 是用户写的原文 |
| `settings` | 见第 8 节 |
| `state` | 本机状态。目前只有 `file_import`，即文件存储的导入记录 |
| `routing_state` | 只有一行：分流方式、"订阅自带"用的配置（也就是当前配置）、"我的方案"用的方案、信息节点表达式 |
| `plans`、`plan_groups`、`plan_rules` | 分流方案（routing.md 第 9 节），C1 开始使用 |
| `backup_targets` | 备份目标，C2 开始使用 |

- 时间都用 Unix 秒。每行有 `updated_at`，只在内容确实变化时更新。`profiles`、`overrides`、`plans`、`plan_groups`、
  `plan_rules`、`backup_targets` 删除时只记 `deleted_at`（软删除），留给备份按记录合并；`profile_contents` 是缓存，
  直接删除。
- `ProfileStore` 的接口与文件存储时相同（`load`、`save`、`read_config`、`write_config`、`delete_config`、
  `read_override`、`write_override`），每个方法是一个事务：
  - `save(list)` 写入整个列表：新配置插入，有变化的更新，列表里没有的标记删除；当前配置写入
    `routing_state.profile_id`。
  - 调用顺序是先 `write_config` 再 `save`，所以列表不会指向没有原文的配置。`Profile.updated_at`、`usage`、
    `proxy_count` 描述的是下载到的原文，因此和原文一起存在 `profile_contents`；还没有原文的配置，读出来时这三项为空。
  - `delete_config` 删除原文，并把这份配置的覆写标记删除。清空覆写文本也是标记删除，再次写入时恢复。
  - 原文不存在时报 `config_missing`；数据库读写失败时报 `storage_failed`，界面显示「操作失败：…」。
  - 如果在写入原文之后、保存列表之前崩溃，留下的原文会在下次打开数据库时清掉。
- `Profile.in_pool` 对应 `profiles.in_pool`，默认为真，目前只存取，C1 才会用到。
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
- `app.main` 先拿到单实例锁（第 8 节），再打开数据库，所以只有一个界面做迁移和导入。退出时关闭连接；没有显式
  关闭的连接在对象回收时关闭。

### 2.3 表结构版本与迁移

- 表结构的版本记在 `PRAGMA user_version`。`database.py` 的 `MIGRATIONS` 是一串迁移函数，第 n 个把版本 n−1 升到 n。
  打开数据库时从当前版本开始依次执行，每一步和版本号的修改在同一个事务里；某一步失败，数据库停在上一个版本，
  下次启动重试。目前只有版本 1，一次建好全部表。
- 增加版本时，在 `MIGRATIONS` 末尾追加一个函数，不改已有的。测试会检查：从每个旧版本迁移上来的数据库，表结构
  与新建的完全相同。
- 文件打不开（不是数据库，或已损坏），或者版本比本程序认识的还新（降级的情况），就把它连同 `-wal`、`-shm`
  改名为 `mihomo-desktop.db.<时间戳>.bad` 保留，再新建一个空库。这和文件存储处理坏索引的方式一致。

### 2.4 从文件存储导入

M7 之前的版本把配置存成文件：

```text
<data>/
├── profiles.json           索引：格式版本 1、当前配置、每份配置的元数据
├── profiles/<id>.yaml      配置原文
├── override.yaml           全局覆写
├── overrides/<id>.yaml     这份配置自己的覆写
└── settings.json           设置（原来的临时 JSON 实现）
```

- 打开数据库时，如果 `state` 里没有 `file_import` 记录，就读取上面这些文件（`file_import.py`），然后在一个事务里
  写入全部数据和 `file_import` 记录。
- 读取规则与文件存储本身一致：文件不存在、JSON 损坏、索引版本不认识，都当作没有，不算失败。索引损坏时不导入
  任何配置，覆写和设置照常导入。索引里有、原文文件却不在的配置照样导入，使用时报 `config_missing`。索引没有
  列出的配置，它的原文和覆写都不导入。设置只导入与默认值不同的项。
- 其他读取错误（如没有权限）或写入失败时，整个事务回滚，不留下一半的数据，文件保持原样，下次启动重试。如果
  重试之前用户已经存了数据，这些数据保持不变：导入的配置排在后面，当前配置、覆写和设置都以库里已有的为准。
- 导入提交后，读过的每个文件都改名加上 `.migrated` 后缀（这个名字已被占用时用 `.migrated.2`，依此类推），不删除。
  改名失败（例如文件被占用）的文件记在 `file_import` 里，下次启动只重试改名，不再导入。
- 有了 `file_import` 记录之后，数据目录里即使再出现文件存储的文件（例如降级到旧版本后又升级回来），也不会再次导入。

## 3. 订阅与切换

- **下载**在用户上下文中进行（urllib，在工作线程里执行），跟随系统代理设置。User-Agent 为
  `mihomo-desktop/<版本> clash.meta`，让订阅服务返回 Clash YAML。只接受 http 和 https；超过一个请求帧能装下的
  大小（16 MiB 减 1 MiB）即拒绝。`Content-Disposition` 里的文件名作为默认名称。
- **导入**：下载或读文件 → 解码为 UTF-8 → 宿主 `validate`（带上全局覆写）→ 写入存储。第一份配置自动成为当前配置。
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
- **编辑**：入口是配置「更多」菜单里的「编辑覆写…」和配置页底部的「全局覆写」一行；有覆写的配置在列表第二行
  写「已覆写」。保存时先用受影响的配置和新覆写调用 `validate`：配置自己的覆写用这份配置校验，全局覆写用
  当前配置校验（所以没有配置时不能编辑全局覆写）。宿主拒绝时不保存，错误显示在编辑框下方；通过后写入存储，
  隧道在运行且用的正是受影响的配置时随后 `reload`。校验、写入和重载在配置库的同一把锁下进行。

## 5. 多语言

界面提供简体中文和英文（决策 D12），用 Qt Linguist 工具链；英文文案的风格以 DESIGN.md 为准。

- **源语言是简体中文**：源码里的文案就是中文界面，`src/mihomo_desktop/translations/en.ts` 是唯一的译文。
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
  2. 环境变量 `MIHOMO_DESKTOP_LANG=zh|en`，用于预览和测试，`scripts/preview.py --lang en` 等价；
  3. 系统的首选界面语言（`QLocale.system().uiLanguages()` 的第一项）：`zh` 开头用中文，其他一律用英文。

  格式化日期的 locale：系统 locale 与界面同一语言时用系统的（保留 en_GB 等地区习惯），否则用该语言的默认
  locale（zh_CN、en_US）。
- **安装**：`app.py` 在创建 ViewModel 和加载 QML 之前调用 `i18n.Translations.apply()`，安装 `QTranslator` 并
  设置 `QLocale.setDefault`。
- **运行中切换**：设置页改了语言后，`app.switch_language` 再调用一次 `apply()`，然后
  `QQmlEngine.retranslate()` 重算 QML 里的 `qsTr`，`ViewModels.retranslate()` 让每个 ViewModel 重新发出文本
  属性的通知。`pages`、`modeOptions`、`sortOptions` 和设置页的选项各有自己的通知信号，而不挂在每秒触发的
  `changed` 上，否则分段控件和菜单会每秒重建。已经显示出来的错误提示保持切换前的语言，直到下一次刷新或操作。
- **流程**：改动文案后运行 `scripts/gen_i18n.py`。`pyside6-lupdate` 扫描 `src/mihomo_desktop` 下的 QML 和
  Python 并更新 `.ts`（不记录行号，移动代码不会改动目录），`pyside6-lrelease` 再编译出 `.qm`。新条目在 `.ts`
  里翻译（手改或用 `pyside6-linguist`），然后再运行一次。`--check` 和 `tests/test_i18n.py` 检查：`.ts`
  覆盖当前源码；英文没有未完成的条目，占位符与源文一致；`.qm` 与 `.ts` 一致。
- **`.qm` 提交进仓库**（与 Theme.qml 一样），从源码运行和预览不需要额外步骤。Nuitka 打包时，
  `package/entry.py` 里的 `nuitka-project` 选项把 `translations/*.qm` 复制到 `mihomo_desktop/translations/`，
  `i18n.py` 按包内路径加载；找不到 `.qm` 时启动报错，而不是静默显示中文。
- Qt 控件自带的文案（如输入框右键菜单的 Cut / Copy / Paste）不在本项目的目录里。Qt 自带的 zh_CN 目录尚未
  收录这些新文案，中文界面下它们仍显示英文。

## 6. 外观

界面有浅色和深色两套外观（决策 D9），取值和规则见 DESIGN.md「Colors → 深色」。

- **主题**：DESIGN.md 的 `colors` 是浅色值，`colors.dark` 是同名角色的深色值。`scripts/gen_theme.py` 把每个
  角色生成为一个绑定，如 `primary: dark ? "#6495FF" : "#1F5EFF"`；`Theme.dark` 取自注入的外观对象。外观变化时
  所有绑定随之重算，界面立即重绘，不需要重启或重新加载 QML。
- **外观对象** `viewmodels/appearance.py` 的 `Appearance`，在 QML 里是 `MihomoDesktop` 模块的类型：

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
  所以无论改动来自设置页还是将来的备份恢复，外观都立即跟上。
- **启动时的模式**（`resolve_mode`）按顺序取第一个有效值：
  1. `app.main(appearance=…)` 的参数，然后是设置项 `appearance`（`system` 视为未指定）；
  2. 环境变量 `MIHOMO_DESKTOP_APPEARANCE=system|light|dark`，用于预览和测试，
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
  `FontLoader` 加载；Nuitka 打包时 `package/entry.py` 把整个目录复制到 `mihomo_desktop/ui/fonts/`。中日韩文字
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

用户的设置由 `models/settings.py` 的 `SettingsStore` 接口提供，只有三件事：读取 `current`、写入
`update(**changes)`、订阅变更 `subscribe(listener)`（回调收到改动前后的 `Settings`，在写入的线程上调用，
返回取消订阅的函数）。设置页（`viewmodels/settings.py`）、概览页的 TUN 与系统代理开关、托盘和关闭窗口的行为
都只依赖这个接口，并且通过订阅跟上变化，不论改动来自哪一页，还是以后从备份恢复。不接受的值抛
`ValueError`，写不进去抛 `SettingsError`（`settings_unwritable`），这两种情况都不通知，设置页标题栏显示
「无法保存设置：…」。

**存储**：`DatabaseSettingsStore` 把设置存在数据库的 `settings` 表里（第 2 节）。每个改过的设置项占一行：`key` 是
字段名，`value` 是 JSON，`scope` 是这一项的范围，`updated_at` 是修改时间。没有对应行或值不被接受的设置项取默认值。
一次 `update` 只写有变化的项，在一个事务里完成，失败时一项都不写。应用里每个数据库只有一个设置存储；直接写
`settings` 表的代码（例如将来的恢复备份）写完后调用 `reload()`，订阅者同样会收到改动前后的 `Settings`。原来临时
实现的 `settings.json` 在第一次启动时导入（2.4 节）。

每个设置项在字段元数据里标明范围（`settings.scope(name)`），供设计备份范围：`synced` 随账户同步，换一台设备
恢复后照样适用；`local` 只在本机生效，恢复到别的设备上没有意义或有风险。

| 设置项 | 取值 | 默认 | 范围 | 含义 |
| --- | --- | --- | --- | --- |
| `language` | `system`、`zh`、`en` | `system` | 同步 | 界面语言（第 5 节） |
| `appearance` | `system`、`light`、`dark` | `system` | 同步 | 外观；主题按它取色 |
| `close_action` | `tray`、`quit` | `tray` | 本机 | 关闭主窗口时留在托盘，还是退出界面；隧道都不受影响 |
| `launch_at_login` | 布尔 | `false` | 本机 | 登录时打开界面。以系统里的登录项为准，界面每次读到系统的状态就写回这里 |
| `tun` | 布尔 | `false` | 本机 | 概览页的 TUN 开关：下一次连接是否开启 TUN |
| `system_proxy` | 布尔 | `false` | 本机 | 概览页的系统代理开关：下一次连接是否设置系统代理 |
| `mixed_port` | 1024–65535 | 7890 | 本机 | 混合端口，总是覆盖配置里的 `mixed-port` |
| `allow_lan` | 布尔 | `false` | 本机 | 允许局域网连接（architecture.md §8"局域网暴露"） |

- **映射到 `RunOptions`**（第 1 节）：Start 用 `Settings.run_options()` 的四项；Reload 的 TUN 和系统代理取自
  `GetStatus`，端口和局域网连接取自设置。
  - **TUN 与系统代理**：开关在概览页，设置页显示同一个 ViewModel 的同一对开关，所以两页总是一致。隧道运行时，
    显示内核报告的状态，拨动开关先改变隧道（TUN 是带新 `RunOptions` 的 Reload，系统代理是
    `SetSystemProxy`），宿主接受后才记入设置；隧道停止时，显示设置里的选择，拨动只改设置，下一次连接生效。
  - **端口与局域网连接**：隧道运行时先带新值 Reload，成功后才写入设置；内核拒绝（如端口被占用，
    `apply_failed`）时保留原值，错误写在对应的行下。隧道停止时直接写入。
  - **开发模式**：开发服务对开启 TUN 或系统代理返回 `unsupported_method`（architecture.md §11）。`ProfileLibrary` 把它换成
    `tun_unsupported`、`system_proxy_unsupported` 或两者都有的 `tun_and_system_proxy_unsupported`，界面在概览页
    标题栏和设置页开关下方显示「开发模式不支持 TUN 模式」等提示，设置不改。
- **外观**：设置页只负责选择和保存。`SettingsViewModel` 的 `appearance` 属性（`system` / `light` / `dark`）和
  `appearanceChanged` 信号供主题使用，`setAppearance(mode)` 修改它。
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
  `mihomo-desktop-<用户名和数据目录的哈希>` 为名监听 `QLocalServer`（只允许同一用户连接）。后来的界面拿不到
  锁，就连上这个名字发一行 `activate`，然后退出；第一个界面收到后显示并激活窗口。Windows 上后来的界面先调用
  `AllowSetForegroundWindow`，让第一个界面能把窗口带到前台。由锁而不是由服务端名字来判断，是因为 macOS 上
  崩溃的界面会留下 socket 文件，而且两个同时启动的界面拿锁时只有一个能成功。
- **登录时打开**（`desktop/login.py`，平台接口 `LoginItem`）：
  - Windows：为当前用户注册一个计划任务（`schtasks /Create /XML`）：登录触发器只针对该用户，以该用户身份、
    交互式令牌、不提权运行，普通优先级，没有运行时限，动作是 `<界面>.exe --background`（界面只进托盘）。
    任务名 `Mihomo Desktop (<域>_<用户名>)`。用计划任务而不是注册表的 `Run` 键，是沿用开发方案的选择：登录后
    立即启动，不受 Windows 对启动项的延迟，安装器（M4）也能用同一个任务名创建和清理。读状态用
    `schtasks /Query /XML`，用户在任务计划程序里禁用的任务视为关闭。
  - macOS：`SMAppService.mainAppService` 的 `register` / `unregister`（macOS 13 起），经 ctypes 调用
    Objective-C 运行时，不依赖 PyObjC。状态为 `requiresApproval` 时设置页提示到系统设置的登录项里允许，并给出
    「打开系统设置」链接。登录项启动的界面目前会显示窗口。
  - 只有打包后的程序（Nuitka 编译，macOS 上还要在 `.app` 里）才注册；从源码运行时是 `UnavailableLoginItem`，
    开关不可用并注明「仅安装版可用」，避免在开发机上把 Python 解释器注册成登录项。测试只用替身，真实注册在
    M4、M5 的测试机上验证。
