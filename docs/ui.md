# 界面

本文面向开发者，说明界面结构与行为约定。日常操作见 [使用指南](usage.md)。

本文描述 Python 界面的结构和各部分的设计：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为。
系统整体的架构见 [architecture.md](architecture.md)，视觉规范见 [DESIGN.md](../DESIGN.md)。备份的设计见
[storage.md](storage.md)，界面部分见第 8 节；自定义分流的设计见 [routing.md](routing.md)，界面部分见第 9 节。
文中的决策编号（D4 等）见 [decisions.md](decisions.md)。

## 0. 结构

- 分层参考 hypervpn：视图 → ViewModel → models → 宿主客户端。界面用 QML，控件基于 Qt Quick Controls 的
  Basic 样式按 DESIGN.md 定制；主题由 `scripts/gen_theme.py` 从 DESIGN.md 生成。
  - `ui/components/` 里的文件引用同目录组件靠隐式导入，它的优先级低于显式导入的模块：Qt 6.10 起
    `QtQuick.Controls` 也有 `SearchField`，所以在那里用 `SearchField` 的文件导入 Controls 之后再写 `import "."`，
    否则拿到的是 Qt 的控件而不是 DESIGN.md 的搜索框。页面目录本来就显式导入 `"../components"`，不受影响。
- **输入框与右键菜单**（DESIGN.md「输入框」「右键菜单」）：
  - 界面里的 `TextField` 和 `TextArea` 只出现在 `AppTextField` 和 `CodeEditor` 里，两者用
    `ContextMenu.menu: null` 关掉 Basic 样式的菜单，由 `EditContextMenu` 在 `ContextMenu.requested` 时打开
    `EditMenu`。菜单第一次用到时才创建；右键时放在指针处（Qt 给文字控件的位置是文字光标，指针位置取自
    `HoverHandler`），键盘的菜单键仍在文字光标处。
  - 行的右键调用 `AppMenu.openContext(更多按钮, 行, 位置)`：它记下指针位置后 `click()` 那个「更多」按钮，按钮
    原来的打开函数照常复制这一行的状态、调用 `openBelow()`，菜单只是改放到指针处；「更多」按钮仍是 `trigger`。
  - `AppMenu` 记住打开时依据的锚点，高度变化时重新决定放在上方还是下方：菜单第一次打开时，列表要等显示后才排好
    菜单项，之前的 `implicitHeight` 只有内边距。
  - 点击别处退出编辑由 `viewmodels/text_editing.ClickAway` 实现：`app.load_ui` 把它装在主窗口上，按下鼠标时
    若焦点在输入框或编辑框里、按下的位置在它的框外，等这次按下处理完后焦点仍在原处，就把焦点交给它的焦点作用域
    （页面或对话框）。
  - 设置行即改即存的输入框用 `SettingField`：`value` 是已保存的值，`committed(text)` 交给 ViewModel 检查和保存。
- **代理页的两栏**（DESIGN.md「代理页」）：代理组和节点各是一个 `ListView`，只创建看得见的行和前后一段缓冲，
  节点的行还会复用。整行可点的列表每行是一个停靠点，但 `ListView` 按创建的先后、而不是行的顺序摆放委托，滚动
  过后 Qt 默认的 Tab 顺序就乱了，所以：
  - 行自己处理 ↑ ↓ 和 Tab：按索引 `positionViewAtIndex` 把目标行做出来再给焦点；越过首尾时沿焦点链找到列表外的
    下一个控件。只有系统让 Tab 停在按钮上时才这样处理（`Application.styleHints.tabFocusBehavior`），否则 Tab
    照常跳过这些行。
  - 从列表外按 Tab 进来时，焦点落在 Qt 认为排在最前的那个委托上，列表再把它移到第一行；Shift+Tab 进来同理移到
    最后一行。直接给某一行焦点（`forceActiveFocus`）不受影响。
  - 获得焦点的行记为列表的 `currentIndex`：`ListView` 不回收也不复用当前行，焦点所在的行滚出视野后仍在原处，
    方向键从它接着走；否则复用会把焦点带到别的行上。
  - 列表只能按矩形裁剪，所以容器的边框画在行的上面，圆角外侧再用页面背景色盖住。
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
- **配置库** `ProfileLibrary`（`models/library/`）让订阅、分流和运行中的隧道保持一致，ViewModel 只经它改动这些。
  各部分共用一个核心（`core.py`：存储、宿主、一把 `asyncio.Lock`，以及读订阅、拼装、校验和重载这几步），每部分管
  一件事：`profiles`（导入、更新、使用和删除订阅，覆写，恢复备份）、`plans`（分流方式、方案、节点池设置和编辑器要
  内核回答的查询）、`exits`（WARP 账户和 VPN Gate 列表）、`networks`（应用内添加的组网）、`tunnel`（连接时的
  顺序，D27；运行中改设置）。改动状态的公开方法在整个改动期间持锁，核心和各部分的内部方法在锁里运行、不再取锁；
  下载、注册这类耗时的请求在锁外。

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
  （storage.md 第 3、6 节）；`models/backup_google.py`、`models/backup_icloud.py`：Google Drive 和 iCloud 两个云端
  提供方（第 4、5 节）；
- `models/plans.py`、`models/services.py`、`models/regions.py`：方案的默认模板与方案 JSON、服务清单、地区名称（第 9 节）。

### 2.1 表

| 表 | 内容 |
| --- | --- |
| `profiles` | 每份配置一行，按 `position` 排序：`id`、`name`、`kind`、`url`、`path`、`auto_update_hours`、`in_pool` |
| `profile_contents` | 配置原文 `yaml`，以及下载时一起得到的信息：`fetched_at`（界面上的"更新于"）、用量四项、`proxy_count`、`has_rules`（内核读出的是否带规则，读之前为 NULL），`unread` 和 `read_yaml`（存下时没有内核能读，第 3 节）。这是本机缓存 |
| `overrides` | `scope` 为 `global` 或某份配置的 `id`；`yaml` 是用户写的原文 |
| `settings` | 见第 8 节 |
| `state` | 本机状态（键值）：各备份目标上次收下的快照摘要；VPN Gate 出口的服务器列表（第 9 节） |
| `routing_state` | 只有一行：分流方式、"订阅自带"用的配置（也就是当前配置）、"我的方案"用的方案、信息节点表达式 |
| `plans`、`plan_groups`、`plan_rules` | 分流方案（routing.md 第 9 节），第 9 节 |
| `backup_targets` | 已开启的云端备份目标（`BackupTargets`），每个一行 |
| `mesh_networks` | 应用内添加的组网网络（mesh.md 10.2 节），不含凭据 |

- 时间都用 Unix 秒。每行有 `updated_at`，只在内容确实变化时更新。`profiles`、`overrides`、`plans`、`plan_groups`、
  `plan_rules`、`mesh_networks`、`backup_targets` 删除时只记 `deleted_at`（软删除），留给备份按记录合并；
  `profile_contents` 是缓存，直接删除。
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
- `app.main` 先拿到单实例锁（第 8 节），再打开数据库，所以只有一个界面做迁移。退出时先关闭连接，再放开锁；
  没有显式关闭的连接在对象回收时关闭。

### 2.3 表结构版本与迁移

- 表结构的版本记在 `PRAGMA user_version`。`database.py` 的 `MIGRATIONS` 是一串迁移函数，第 n 个把版本 n−1 升到 n。
  打开数据库时从当前版本开始依次执行，每一步和版本号的修改在同一个事务里；某一步失败，数据库停在上一个版本，
  下次启动重试。版本 1 是 1.0.0 的表结构，由 `_schema_1` 一次建好全部表。
- 改表结构就在 `MIGRATIONS` 末尾追加一个迁移，已有的迁移（包括 `_schema_1`）不再改动。`tests/test_database.py`
  检查从每个旧版本升上来的表结构和新建的相同；新的迁移另写测试，确认升级前的数据升级后仍在、读得出。这条规则也在
  [maintenance.md](maintenance.md) 第 5 节，存下来的格式（配置、备份快照、方案 JSON）同样要让以后的版本读得懂
  （storage.md 第 2 节）。
- 文件打不开（不是数据库，或已损坏），或者版本比本程序认识的还新（降级的情况），就把它连同 `-wal`、`-shm`
  改名为 `solaris.db.<时间戳>.bad` 保留，再新建一个空库。

## 3. 订阅与切换

- **下载**在用户上下文中进行（urllib，在工作线程里执行），跟随系统代理设置。User-Agent 为
  `solaris/<版本> clash.meta`，让订阅服务返回 Clash YAML。只接受 http 和 https；超过一个请求帧能装下的
  大小（16 MiB 减 1 MiB）即拒绝。`Content-Disposition` 里的文件名作为默认名称。
- **导入**：下载或读文件 → 解码为 UTF-8 → `inspect_source`，带规则的再经宿主 `validate`（带上全局覆写）→ 写入
  存储。当前配置还空着时，第一份带规则的订阅成为当前配置（第 9 节）。
- **节点链接**：「导入节点」接收每行一个分享 URI，去掉空行和完全相同的重复行后保存原文；协议转换仍由内核负责。
  来源存为无外部路径的本地配置（`kind=file`、`path=""`），随备份保留，不下载也不自动更新。
  内核可用时必须把每行都解析为节点，不能悄悄丢弃错误行；不可用时沿用 D27，连接后再检查。
- **编辑信息**：订阅行的菜单打开名称、地址和自动更新设置表单，本地来源只改名称。导入和编辑共用 `AutoUpdateField`，
  选择是否自动更新并填写正整数小时；间隔随导入草稿保留，也随元数据持久化及备份。关闭时间隔仍以负数保存，
  之后在菜单里重新勾选「自动更新」会沿用原来的间隔，而不是退回 12 小时。保持原 ID、覆写和节点池引用。
  地址改变后保留缓存配置、清除旧地址的用量和更新时间；每次地址变更递增内存中的来源代次，手动和定时更新只提交
  仍对应同一代次及来源的结果，过期下载的成功或失败均不覆盖新设置。节点池中的来源改名时重新检查并重载方案，
  因为内核会用来源名称区分同名节点。
- **没有内核能读时**（D27）：macOS 的内核只在隧道连接时运行（`TunnelHost.core_available` 为假）。这时导入只把
  原文写入存储，标为未读（`unread`）：没有节点数，是否带规则未知，订阅行写「连接后读取」。手动更新同样存为未读，
  但内核上次读过的原文留在 `read_yaml`；自动更新等到有内核时再做。连接时 `tunnel.start` 先 `prepare_start` 让
  隧道起来、扩展能回答，再按列表顺序读未读的订阅（`inspect_source`，带规则的再 `validate`），补上导入本该定下的
  （分流方式、当前配置），然后才 `Start`。内核拒绝更新来的原文时，退回 `read_yaml` 里上次读过的，和更新失败
  一样记为「更新失败」；导入的没有可退回的，仍是未读，"我的方案"拼装时不带它们，下次连接再读。正要使用的那份
  仍是未读时断开隧道并报错。Windows 的服务一直在，导入照旧当场读。
- **自动更新**：订阅默认每 12 小时更新一次，可在导入或编辑时按订阅调整间隔，也可以关闭。间隔从上次成功下载起计算，
  界面每分钟检查一次到期的订阅，在启动时也检查；
  失败的订阅 30 分钟内不重试，行内显示「更新失败」。原文已写入、但运行中的隧道没能重载的也算失败，30 分钟后
  照样再试。
- **切换与更新**：更新先 `validate` 再写入存储。新原文正被使用时（"订阅自带"用的那一份，或"我的方案"节点池里
  的，routing.md 5.4 节），隧道在运行就随后 `reload`；手动更新和自动更新相同。原文和存储里的相同时只记下更新
  时间和用量，不写原文、不重载：隧道跑的就是它，而 mihomo 重载时会关掉那一刻新来的连接、清掉测过的延迟。上次
  更新写入原文后失败了（例如重载被拒绝）时，原文不变也照样重载。切换配置时，隧道在运行就先 `reload`，成功后才
  记下新的当前配置，核心拒绝时保留原来的配置；隧道未运行只记下选择。下载和校验不持有锁，写入存储和重载在同一
  把锁下串行执行。
- **更新之后**：`profiles.update` 和 `update_due` 返回 `Updated`，说明原文变没变（`changed`）、新原文有没有被
  使用（`applied`）。原文变了，订阅页和分流页重读每份订阅的规则数和节点池；被使用的，代理页和概览重读策略组，
  分流页正显示时重读内核在跑的规则（"订阅自带"的规则表），不显示的等打开时再读。这些都在后台读，窗口不等。
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
  3. 系统的首选界面语言（`i18n.system_ui_languages()` 的第一项）：`zh` 开头（`zh-Hans-CN`、`zh-Hans`、
     `zh-CN`、`zh-Hant-TW` 等）用中文，其他一律用英文。

  格式化日期的 locale：系统 locale 与界面同一语言时用系统的（保留 en_GB 等地区习惯），否则用该语言的默认
  locale（zh_CN、en_US）。

  系统语言和 locale 在 Windows 上取自 `QLocale.system()`。macOS 上不能用它：PySide6 把 `QLocale.system()`
  改成按 Python 的 `locale.getlocale()` 构造，也就是看 `LANG`、`LC_*`，而从 Finder 打开的应用没有这些变量，
  得到的是 `C`，跟随系统就成了英文。所以 macOS 上 `i18n` 和 Qt 自己的后端一样直接问 CoreFoundation：
  语言列表用 `CFLocaleCopyPreferredLanguages`（用户在系统设置里排的顺序，不受应用本地化影响），locale 用
  `CFLocaleCopyCurrent`（语言按 Info.plist 的 `CFBundleLocalizations`，即 en、zh-Hans 匹配）。从终端运行时
  `LANG` 同样不影响语言选择，要临时换语言用 `SOLARIS_LANG`。
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
- **Qt 自带的文案**：输入框和编辑框的右键菜单是本项目的 `EditMenu`（DESIGN.md「右键菜单」），文案在 `en.ts` 里，
  与其他界面文案一样翻译；Basic 样式自带的那份菜单（`TextEditingContextMenu`）不会出现，也就不需要 Qt 的目录。
  其他 Qt 自带的文案（如系统文件对话框）由操作系统提供，跟随系统语言。
- **macOS 扩展用途说明**（D36）：扩展的 `InfoPlist.strings` 提供中文、英文，基础 plist 英文回退；
  跟随 macOS 语言偏好，不受设置页语言开关影响。安装/停用提示的系统按钮和措辞由 macOS 管理；
  应用内安装指引与停用确认框仍跟随应用语言。

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
- **Qt 控件的调色板**：Basic 样式里本项目没有重绘的部分（滚动条、Qt 自带的工具提示、文字选区的默认值）读窗口的
  `palette`，Main.qml 把它映射到 token，浮层继承窗口的调色板：

  | palette | token |
  | --- | --- |
  | `window` | `background` |
  | `windowText`、`text`、`buttonText`、`toolTipText` | `text` |
  | `base`、`button`、`toolTipBase` | `surface` |
  | `alternateBase` | `surface-subtle` |
  | `light` | `surface-selected` |
  | `midlight` | `border` |
  | `mid`（滚动条滑块） | `border-control` |
  | `dark`（按下的滑块） | `text-muted` |
  | `placeholderText` | `text-muted` |
  | `highlight`、`accent` | `primary` |
  | `highlightedText`、`brightText` | `on-primary` |
  | `link`、`linkVisited` | `link` |
  | `shadow` | `scrim` |

- **字体**：Windows 和 macOS 的 Qt 界面共用 Inter（400、500、600）、JetBrains Mono（400）、
  Noto Sans SC（可变字体，使用 400、500、600）、Noto Sans Symbols 2 和 Noto Color Emoji（COLRv1 + SVG，含旗帜）。
  Emoji 使用 Google Fonts 的混合字体，保留 SVG 彩色字形供 macOS 26 的 CoreText 绘制；不能换成仅含 COLRv1 的版本，
  否则字体注册与连字检查可能通过，实际绘制却没有颜色。固定来源和许可证见 `sources.json`。
  `fonts.py` 在加载 QML 前注册资源，设置字体族回退与独立的 Emoji 回退；`Fonts.qml` 只解析已注册的字体。
  PySide 未暴露 Qt 的文字体系回退接口，使用 `QFont.insertSubstitutions` 为 Inter 与 JetBrains Mono
  指定 Noto Sans SC、Noto Sans Symbols 2 的有序补充字体；Emoji 使用 `setApplicationEmojiFontFamilies`。
  来源、文件大小和 SHA-256 固定在 `ui/fonts/sources.json`，离线随包分发，各带 `OFL.txt`；
  `package/entry.py` 将整个目录复制到两端的 `solaris/ui/fonts/`。注册失败记入诊断日志，系统字体仅用于缺字或资源异常兜底。
  SIL OFL 1.1 字体与本项目的 GPL-3.0 代码分别遵循各自许可。
- **Unicode**：完整规则见 DESIGN.md「Unicode 与文本编码」。配置读入严格使用 `utf-8-sig`，文本导出使用
  UTF-8 无 BOM 与 LF；原文、名称引用、凭据不做 Unicode 规范化。Qt 的省略与编辑使用字素边界，不能用
  Python 码点下标解释 Qt 的 UTF-16 光标位置。`TextEditing.prepareDeletion` 在普通退格/Delete 前选中完整字素，
  再让 Qt 执行删除，保留编辑信号与撤销栈；只读、输入法组合和带修饰键的操作交给原生控件。
  `text.utf8_prefix` 仅用于受字节数限制的云端摘要，保留完整字素；
  备份正文仍保存完整原文。新字体或 Qt 升级必须运行 `tests/test_fonts.py` 与 `tests/test_text.py`。
- **长名称**（DESIGN.md「长名称」）：`UiText` 被截断（`truncated`）时才用一个 `Loader` 建出悬停处理和
  `AppToolTip`，完整显示的文字不多建对象；所以名称只要设了 `elide` 和宽度约束，悬停提示随之而来。名称在
  按钮或行里时，按钮 `Layout.fillWidth` 加 `Layout.maximumWidth: implicitWidth`，同一行的标签用
  `Layout.minimumWidth` 保住宽度；嵌在固定文字里的名称用 `Text.ElideMiddle`。`AppMenu` 的宽度以
  `menu-max-width` 为上限。`scripts/preview.py --long-names` 用 `tests/support/simulated.py` 里的
  `LONG_*` 样本（中文、英文、Emoji）给订阅、节点、策略组、方案、组网和连接起长名称。
- **测试**：`tests/test_qml.py` 检查 Theme.qml 与 DESIGN.md 一致、两组颜色角色相同，并按 DESIGN.md 的用法
  核对两种外观的对比度；`tests/test_appearance.py` 用 `tests/support/appearance.py` 模拟系统切换外观；
  `tests/test_app.py` 在浅色和深色下加载界面，并检查系统切换后窗口立即重绘。
  字体与编码专项可运行 `uv run pytest -q tests/test_fonts.py tests/test_text.py tests/test_logs.py tests/test_licenses.py`：
  检查实际字形所属字体、中文 400/500/600、Emoji 连字与彩色绘制、完整字素省略和删除、撤销/重做、只读/输入法组合、
  复制粘贴、严格解码、数据库/备份与日志原文往返。设置 `QT_SCALE_FACTOR=1.25`、`1.5`、`2` 分别重跑字体测试；
  CI 的 macOS arm64/x64、Windows x64/ARM64 Python 测试均包含这些用例。自动化中的输入法预编辑事件不能替代真实候选窗验证；
  Windows 显示效果及原生输入法仍须在对应系统上验收。

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
字段名，`value` 是 JSON，`updated_at` 是修改时间。没有对应行或值不被接受的设置项取默认值。
一次 `update` 只写有变化的项，在一个事务里完成，失败时一项都不写。应用里每个数据库只有一个设置存储；直接写
`settings` 表的代码（恢复备份）写完后调用 `reload()`，订阅者同样会收到改动前后的 `Settings`。

每个设置项在字段元数据里标明范围（`settings.scope(name)`），备份只带 `synced` 的（storage.md 第 3 节）：`synced`
随账户同步，换一台设备恢复后照样适用；`local` 只在本机生效，恢复到别的设备上没有意义或有风险。

| 设置项 | 取值 | 默认 | 范围 | 含义 |
| --- | --- | --- | --- | --- |
| `language` | `system`、`zh`、`en` | `system` | 同步 | 界面语言（第 5 节） |
| `appearance` | `system`、`light`、`dark` | `system` | 同步 | 外观；主题按它取色 |
| `close_action` | `tray`、`quit` | `tray` | 本机 | 关闭主窗口时留在托盘，隧道继续运行；还是退出 Solaris，先断开隧道（D41） |
| `launch_at_login` | 布尔 | `false` | 本机 | 登录时打开界面。以系统里的登录项为准，界面每次读到系统的状态就写回这里 |
| `tun` | 布尔 | `true` | 本机 | 设置页「接管方式」的 TUN 开关：连接时是否开启 TUN。macOS 上总是开启，不看这一项 |
| `system_proxy` | 布尔 | `true` | 本机 | 设置页「接管方式」的系统代理开关：连接时是否设置系统代理。只有 Windows 服务设置系统代理 |
| `mixed_port` | 1024–65535 | 7890 | 本机 | 混合端口，总是覆盖配置里的 `mixed-port` |
| `allow_lan` | 布尔 | `false` | 本机 | 允许局域网连接（architecture.md §8"局域网暴露"） |
| `tun_stack` | `profile`、`system`、`mips`、`gvisor`、`mixed` | `mips` | 本机 | "TUN 协议栈"（D3）：`profile` 是"跟随配置"，用配置里的 `tun.stack`；其余写进 `RunOptions.tun_stack`，盖过配置和覆写（architecture.md §3.1、§3.4）。默认 `mips`，即 mihomo 自己的默认栈 |
| `include_all_networks` | 布尔 | `false` | 本机 | 仅 macOS（D19）："包含所有网络"，把系统默认绕开隧道的流量也送进隧道，局域网流量除外。写进 VPN 配置的 `includeAllNetworks` 和 `excludeLocalNetworks`，下次连接时生效；打开后 TUN 协议栈不能用 `system` 和 `mixed`（architecture.md §3.4） |
| `delay_test` | `http`、`tcp` | `http` | 同步 | "延迟测试"（D32）：代理页「测速」经节点请求测试地址（HTTP），还是只计到节点服务器的 TCP 握手。代理页、概览页和策略组编辑器显示所选测试的结果（architecture.md §4.3） |
| `warp_protocol` | `masque-h2`、`masque-h3`、`wireguard` | `masque-h2` | 本机 | WARP 出口的协议，在分流页的出口设置对话框里改，设置页不列出。账户只有一种协议的密钥，改了协议就为它换密钥（exits.md 3.1、3.2 节） |
| `update_check` | 布尔 | `true` | 同步 | 自动检查更新：启动 30 秒后检查一次，之后按 `update_check_hours` 定期检查（release.md 第 2 节） |
| `update_check_hours` | `24`、`72`、`168` | `24` | 同步 | 自动检查的间隔：每天、每 3 天、每周 |

- **本地崩溃报告**（D37）：设置页「关于」提供独立行。`AboutViewModel` 每分钟在后台读取概况，显示可导出数量；
  导出先确认原始内容范围，再选择路径。清除先确认且默认取消，只清除界面日志，不改订阅和凭据；
  系统与服务报告保持归属。操作失败在行内显示，普通诊断只包含概况。
  `bootstrap` 在 Qt 导入前安装 `Recorder`；`AsyncRunner` 的错误已有调用者处理时不重复记作崩溃。
  记录器写入失败不覆盖原异常，也不阻止启动。
- **映射到 `RunOptions`**（第 1 节）：Start 用 `Settings.run_options()` 的各项（`tun_stack` 为 `profile` 时传空串）；
  Reload 的 TUN 和系统代理取自 `GetStatus`，其余取自设置。
  - **连接开关**（D26，DESIGN.md「连接开关」）：概览页「出站」分组第一行的「连接」是界面上唯一的连接控件，托盘
    菜单的「连接 / 断开」照旧。打开即按设置连接，关闭即 `Stop`。进行中开关不可操作，第二行写「正在连接…」
    「正在断开…」，宿主报告 `starting`、`stopping` 时也一样；操作完成后等下一次刷新读到隧道的新状态才结束，
    开关不会先跳回原位。连不上时开关回到关闭，第二行用 `danger` 写原因，留到下一次操作。已连接时概览页标题栏
    写接管方式和运行时长：「TUN 与系统代理 · 已运行 2:14:36」，只开一项时写「TUN」或「系统代理」，两项都没开时
    写「混合端口 7890」。
  - **Windows 唤醒恢复**：`GetStatus` 的 `recovery_state`、`recovery_error` 映射到 `HostStatus`，由概览和托盘共用。
    sleeping、waiting_network、connecting 时标题写「正在恢复连接」，开关保持开启且可关闭，行内说明当前阶段和取消方式。
    关闭发送真正的 Stop，即使此刻内核已经临时停止，也会取消服务的恢复意图。failed 时开关关闭、标题写「恢复连接失败」，
    行内写网络未恢复、内核未响应或普通恢复失败的原因，允许再次连接；成功或主动断开后清除恢复提示。
  - **macOS 扩展停用**：安装版的设置页在右列「启动」之后显示「系统扩展」，普通用户权限下经管理库发起操作。
    「停用…」先确认：断开连接、移除 Solaris 的 VPN 配置，保留本机数据。`SystemExtensionViewModel` 的动作经
    `library.tunnel.deactivate_extension()` 与配置更新、连接共用模型锁；`MacExtensionHost` 再串行原生启停与维护操作。
    排队、原生调用与回调都计入期限。进行中禁用连接，结束后概览等待新的状态快照，旧快照不会把开关短暂改回开启。
    系统待处理、取消、失败、完成分别呈现，完成后下次连接重新激活；Windows 和开发服务不显示这个入口。
  - **macOS 网络扩展指引**：连接返回 `extension_needs_approval` 时，概览写「等待授权」，连接行写「允许网络扩展后继续连接」
    并提供「安装指引…」。主窗口显示、切回概览，打开 `ExtensionSetupDialog`，托盘连接也走同一条路径。
    指引列出「系统设置 › 通用 › 登录项与扩展」以及「网络扩展」信息按钮、Solaris 开关，提供「打开系统设置」和
    「继续连接」。「打开系统设置」复用 `SMAppService.openSystemSettingsLoginItems()`，只打开设置，不注册登录项；
    无法打开时保留手动路径。位置参照 [Apple 使用手册](https://support.apple.com/guide/mac-help/mtusr003/mac)。
    关闭指引不自动重试，连接行保留入口；用户继续时才重试，未授权就继续显示指引，成功后关闭并清除提示。
    重试期间禁用操作和 Esc，避免重复连接或误以为取消了进行中的连接。其他连接错误照常显示在连接行，指引关闭。
    系统返回 `willCompleteAfterReboot` 时仍通过 `extension_needs_reboot` 保留原始结果，界面显示「网络扩展尚未就绪」，
    提醒按系统提示完成设置，并提供「重试连接」。不把重启写成正常安装步骤，不因缓存的上一次结果阻止重试；
    每次重试重新经过宿主激活流程，成功后关闭指引并清除提示。这两种结果都有中英文文案，不显示原始的英文错误，
    也不报作连接失败。
  - **其他用户的隧道**（Windows，architecture.md §5.4）：另一个用户的隧道在运行时，概览页标题栏写「其他用户正在
    使用 · 另一位用户已连接，对方断开后才能在这里连接」，连接开关和托盘的「连接」不可用。管理员（D5b）看到的是
    「其他用户正在使用 · 另一位用户已连接」，右侧多一个「接管」按钮。它先弹出破坏性确认框「接管其他用户的隧道？」，
    正文「另一位用户的连接会断开，隧道改由你使用。需要管理员权限。」；确认后 UAC 弹出提示，标题栏写「正在接管…」，
    直到服务为新属主重启完。UAC 里选「否」时什么都不变；接管辅助进程失败时标题栏写「无法接管隧道」。接管后隧道是
    断开的，由用户自己连接。是不是管理员由组装时的 `take_over` 决定（`app.default_take_over`，只有 Windows 上装好
    的服务才有），`OverviewViewModel.canTakeOver` 只在其他用户的隧道运行时为真。
  - **侧栏的「代理」**（D27，DESIGN.md「侧栏导航」）：代理页列的是运行中内核的策略组，内核只在隧道运行时回答
    `ListGroups`，所以「代理」只在连接开关开着、不在进行中时出现：概览的 `connected` 为真且 `connectionChanging`
    为假，组装时交给 `ShellViewModel.tunnel_running`（`TUNNEL_PAGES`）。正在连接、正在断开（开关一关就算）、宿主
    报告 `starting` 或 `stopping`、唤醒后正在恢复或恢复失败、其他用户的隧道在运行（D5b，接管期间也是）、服务
    不可用或刷新失败时都不列，两个平台相同。不列时 `setPage("proxies")` 不起作用，概览页的当前节点按钮禁用。
    停在代理页时隧道停止，先发 `leavingPage`，窗口把代理页里的焦点移到「概览」，再切回概览、去掉这一项；焦点在
    「代理」上时移到当前页的导航项，键盘放上去的仍带焦点框。侧栏的页面是 `KeyedListModel`，增删「代理」时其余
    导航项原地保留，焦点、Tab 顺序和读屏名称不受影响。页面切换没有快捷键，启动时总是打开概览，不恢复上次的页面；
    安装链接只打开订阅页，托盘菜单不打开页面。
  - **接管方式**：连接时能接管什么由宿主决定（`TunnelHost.takeover`）。宿主发出的每份 `RunOptions` 都先经
    `taken_over` 改写，不论设置怎么写：

    | 宿主 | `takeover` | 设置页「接管方式」 | 连接时 |
    | --- | --- | --- | --- |
    | Windows 服务 | `settings` | 「TUN 模式」「系统代理」两个开关，全新安装默认只开启 TUN | 按开关 |
    | macOS 扩展 | `tun` | 「TUN 模式」一行右侧写「已启用」，第二行「macOS 上总是启用」；没有系统代理一行 | 总是 TUN，不设系统代理 |
    | 开发服务 | `none` | 两行右侧都写「不使用」，分组标题右侧写「开发模式只开放混合端口」 | 只开放混合端口 |

    分组标题右侧平时写「连接时使用」。两个开关由 `OverviewViewModel` 提供，因为要显示隧道的状态：隧道运行时
    显示内核报告的状态，拨动先改变隧道（TUN 是带新 `RunOptions` 的 Reload，系统代理是 `SetSystemProxy`），
    宿主接受后才记入设置，所以已连接时改这两项立即生效；隧道停止时显示设置里的选择，拨动只改设置。
  - **两项都关闭**（Windows）：照常连接，内核只开放混合端口（不允许局域网连接时只在回环地址上），系统的设置
    一项不改，要走代理的程序自己指向这个端口。系统代理一行第二行写「两项都关闭时，连接后只开放混合端口」。
  - **只开系统代理**（Windows）：程序经系统代理只走 TCP，UDP 直接发出。系统代理一行第二行写「游戏、语音通话等
    UDP 流量不经过系统代理，需要 TUN 模式」。
  - **macOS 不设系统代理**：扩展不提供 `SetSystemProxy`（architecture.md §6.3），TUN 已经接管全部流量，所以
    不列这一行，`system_proxy` 在 macOS 上不起作用。
  - **端口、局域网连接与 TUN 协议栈**：隧道运行时先带新值 Reload，成功后才写入设置；内核拒绝（如端口被占用，
    `apply_failed`；内核不带 `with_gvisor` 时选了 `gvisor` 或 `mixed`，`invalid_config`）时保留原值，错误写在对应的
    行下。隧道停止时直接写入。
  - **TUN 协议栈**在"接管方式"分组里 TUN 模式的下一行，是下拉框：跟随配置、system、mips、gvisor、mixed，默认
    mips。栈名照 mihomo 配置里 `tun.stack` 的值写成小写，不翻译。
  - **包含所有网络**是它下面的开关，只在 macOS 上显示，说明写着「系统默认绕开隧道的流量也进入隧道，下次连接时
    生效」。打开它只写设置，不动运行中的隧道；打开时 TUN 协议栈若是 `system` 或 `mixed`，就和设置一起改成
    `gvisor`，这一步像改栈一样先带新值 Reload。打开期间下拉框里的 `system`、`mixed` 不可选，注记「包含所有网络时
    不可用」。
  - **开发模式**：开发服务对开启 TUN 或系统代理返回 `unsupported_method`（architecture.md §11）。Windows 以外的
    服务都是开发服务，`app.default_host` 给它的 `ServiceHost` 是 `TAKEOVER_NONE`，界面不向它要这两项，所以两项
    默认打开时开发模式照样能连接，只开放混合端口。直接向开发服务要求时，`ProfileLibrary` 仍把拒绝换成
    `tun_unsupported`、`system_proxy_unsupported` 或两者都有的 `tun_and_system_proxy_unsupported`，界面显示
    「开发模式不支持 TUN 模式」等提示，设置不改。
- **延迟测试**（D32）：设置页右栏第一个分组「代理」里的一行，分段控件「HTTP」「TCP 握手」，改了立即生效，
  不重测。选 TCP 握手时第二行写「自动选择节点的组仍用 HTTP 测速」。
  - HTTP 经节点请求组的测试地址，测得的是经代理的延迟，同时说明节点可用；TCP 握手只计内核到节点服务器的连接
    时间，说明能连上，不说明代理可用。
  - 只管用户发起的测速：代理页的「测速」按它调用 `health_check(group, test)`。自动选择、故障转移、负载均衡组照旧
    按 mihomo 自己的 URL 测试定时检查和选择，URL 测试没有 TCP 方式；TCP 的测速也不像 HTTP 那样让这些组重新选择。
  - 显示：`Proxy.delay(test)` 取所选测试的结果，代理页的行、按延迟排序、概览页的当前节点和策略组编辑器的预览都用它。
    两种结果在内核里各自保留，换设置后显示另一种已有的结果，没测过是「—」。内核在应用下一份配置时清空 TCP 结果。
  - 没有 TCP 服务器可拨的节点（Hysteria2、TUIC、WireGuard 等）、经 `dialer-proxy` 的节点（WARP、VPN Gate 出口）
    和直连、拒绝在 TCP 下总是「—」；组显示它此刻所用节点的结果。VPN Gate 出口的检查只看 URL 测试的结果。
- **外观**：设置页只负责选择和保存（`setAppearance` 写设置项 `appearance`），主题跟随第 6 节的外观对象。
- **托盘**用 `Qt.labs.platform` 的 `SystemTrayIcon`（`ui/components/TrayIcon.qml`）。它在 macOS 和 Windows 上
  有 Qt 平台插件的原生实现，`QGuiApplication` 就够，不必为 `QSystemTrayIcon` 引入 Qt Widgets 和
  `QApplication`。没有原生托盘的平台（Linux、测试用的 offscreen）上 Labs Platform 会要求 Qt Widgets，所以
  只在 `cocoa` 和 `windows` 上创建托盘，其余平台关闭窗口即退出 Solaris。
  - 菜单：连接状态（不可点）、连接或断开、出站模式子菜单、打开主窗口、「退出 Solaris」（退出前断开期间不可点）。
    这是系统原生菜单，外观跟随平台，不按 DESIGN.md 的窗口内菜单绘制。
  - 图标在 `ui/tray/`，是侧栏标记的线条图形，未连接时调成 45% 不透明度。macOS 用模板图（只看 alpha，由菜单栏
    着色）；Windows 按任务栏主题（注册表 `SystemUsesLightTheme`，Qt 报告的是应用主题，不是任务栏的）选深色或
    白色图形，每 10 秒和系统配色变化时重读。
- **标题带**（DESIGN.md「顶部区域」）：`viewmodels/titlebar.py` 的 `TitleBar` 给 Main.qml 窗口标志和平台样式，
  平台调用在 `desktop/titlebar.py`，Main.qml 创建完成后调用 `attach`。
  - 窗口用 Qt 的 `ExpandedClientAreaHint` 和 `NoTitleBarBackgroundHint` 去掉系统标题栏，内容铺到上沿；
    `ApplicationWindow` 的 `topPadding` 设为 0，不按安全边距下移。Qt 按有标题栏时的外框创建窗口，窗口会高出
    标题栏的高度，所以 Main.qml 随后把大小设回 1280 × 800。macOS 上原生窗口隐藏时会销毁、显示时重建
    （见「关闭窗口」的「隐藏时释放」），重建后由 `TitleBar` 还原。
  - 拖动和双击：侧栏顶部和页面标题栏（`PageHeader`，向上、向两侧盖住页边距）下面各垫一层 `TitleBandArea`。
    拖过阈值后调用 `startSystemMove()`，由系统移动窗口，贴靠照常；双击按 `TitleBar.doubleClickAction()`
    缩放或最小化。控件在它上面，点击归控件。页面滚动后，滚进标题带的内容不能拖动窗口，侧栏顶部始终可以。
  - macOS：红绿灯由系统绘制和定位。侧栏上内边距取 `sidebar-top-macos` 与"安全边距上沿 + 24"中较大的一个，
    系统标题栏更高时标记跟着下移。Qt 不隐藏窗口标题，`attach` 经 Objective-C 运行时把 `titleVisibility`
    设为隐藏，读屏和「窗口」菜单仍有标题。双击读系统设置 `AppleActionOnDoubleClick`：`Minimize` 最小化，
    `None` 不动，其余缩放。
  - Windows：窗口标志再加 `CustomizeWindowHint` 和 `WindowSystemMenuHint`，不带按钮提示，Qt 就不画自己的
    标题栏，边框、阴影、圆角和边缘缩放仍由系统提供。三个按钮由 `CaptionButtons.qml` 绘制。`attach` 装上
    原生事件过滤器 `WindowsFrame`：
    - 给窗口样式补上 Qt 在没有按钮提示时去掉的 `WS_MINIMIZEBOX`、`WS_MAXIMIZEBOX`，任务栏点击最小化、
      Win+↑↓、贴靠和系统菜单里的这两项才可用；
    - 自己回答 `WM_NCHITTEST`：两侧和下边框交给 `DefWindowProc`；未最大化时上边缘的缩放区返回 `HTTOP`；
      最大化按钮的矩形（QML 写进 `TitleBar.maximizeArea`）返回 `HTMAXBUTTON`，悬停时系统弹出贴靠布局；
      其余是客户区，由 QML 处理；
    - 最大化按钮因此收到非客户区消息：悬停和按下转成 `maximizeHovered`、`maximizePressed` 供按钮显示，
      按下和松开不交给系统（否则系统会画出旧式按钮），松开时发出 `maximizeClicked`；
    - Alt+空格：Qt 自己弹出的系统菜单按窗口标志把最小化、最大化置灰，所以截下这个按键，改发
      `WM_SYSCOMMAND`（`SC_KEYMENU`），由系统弹出原生菜单。
  - 其他平台（如测试用的 offscreen）只有布局，不做平台调用。`tests/test_titlebar.py` 在两种样式下检查布局、
    读屏名称、Tab 顺序和双击，并用替身检查 Windows 的命中测试和消息处理；系统实际怎么响应只能在 Windows 上看。
- **关闭窗口**：`tray`（默认）时隐藏窗口，界面留在托盘，隧道继续运行；托盘菜单的「打开主窗口」恢复窗口，Windows
  上点托盘图标也可以。`quit` 时先隐藏窗口，再像下面一样退出 Solaris。设置页这一行的说明随选项变：「隧道继续运行」
  或「先断开连接，再退出」。
  - macOS 上窗口隐藏时 Solaris 只留在菜单栏：`desktop/dock.py` 经 Objective-C 运行时把 `NSApp` 的激活策略设为
    Accessory，Dock 和 ⌘Tab 里都没有它。窗口无论从哪里显示（菜单栏的「打开主窗口」、再次打开 Solaris、单实例
    转发、一键导入链接、「未能断开连接」对话框、系统扩展的授权引导），都先设回 Regular，再调用 `activate`
    激活应用；只显示窗口的话，顶部菜单栏仍是原来前台应用的。
  - 策略只跟着窗口的 `visible` 走：Main.qml 在 `visible` 变化时调用 `DesktopViewModel.setWindowShown`，不在各个
    入口分别处理。最小化的窗口仍算显示，留在 Dock 里。关闭动作为 `quit` 时，断开期间 Dock 图标随窗口一起隐藏，
    断开失败时随对话框回来。没有托盘时不切换（关闭窗口就退出）；其他平台和测试用的 offscreen 不做平台调用，
    `tests/test_desktop.py`、`tests/test_app.py` 用替身记录调用。
  - 再次打开 Solaris（如从访达或「启动台」）时，系统发来 reopen，Qt 把它和其他激活一样报告为应用激活，窗口隐藏时
    就显示出来（`DesktopViewModel.reopened`）。
  - 带 `--background` 启动时（登录时打开，macOS 的登录项不带它），`DesktopViewModel.start` 在进入事件循环前
    切到 Accessory。Qt 在启动收尾时会请求激活应用，Accessory 应用会被允许，而 Qt 把这次激活也报告为应用激活，
    窗口就会弹出来；所以 `app.main` 先设置 Qt 的 `QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM`
    （`dock.open_in_menu_bar`），Qt 既不把进程转为前台应用，也不请求激活。Qt 在事件循环开始后还会读它，所以
    整个进程都保留；界面启动的子进程（codesign、schtasks）不是 Qt 程序。
  - **隐藏时释放**：窗口隐藏后界面进程仍常驻，只剩托盘或菜单栏时把只为画窗口占的内存还回去。窗口隐藏 2 秒后
    （Main.qml 的 `releaseTimer`，窗口马上又显示、或关闭即退出时不做），Main.qml 卸下七个页面，再调用
    `DesktopViewModel.releaseWindow`，由 `desktop/memory.py` 删除卸下的页面，回收 QML 和 Python 的垃圾，清空
    `QPixmapCache`，临时关掉 `persistentGraphics` 和 `persistentSceneGraph` 调用 `releaseResources()`，放掉
    场景图、字形和纹理缓存、图形设备和 Qt Quick 的图片缓存。之后仍是持久的，2 秒内隐藏又显示不必重建。
    - 用 CurveRenderer 画的 `Shape`（`Icon`、Windows 的 `CaptionButtons`、流量图 `TrafficChart`）释放后重新创建：
      Qt 6.12 的 CurveRenderer 只在路径变化时生成场景图节点，已经画过的 `Shape` 在显示时新建的场景图上什么也不画，
      侧栏的标记和导航图标、标题栏按钮因此变空白（#45），窗口上开着对话框而留下的页面也一样。三处各把 `Shape` 放在
      一个 `Loader` 里，`active` 跟随单例 `Graphics.shapes`；Main.qml 在 `releaseWindow` 之后调用 `Graphics.renew()`，
      趁窗口隐藏全部重建一次，macOS 销毁原生窗口、Windows 保留原生窗口两条路都经过这里。不改用 `GeometryRenderer`：
      它的抗锯齿要另开多重采样，图标边缘会变。测试用的 offscreen 平台走软件场景图，没有这个问题，
      `tests/test_hidden_window.py` 检查这些 `Shape` 是新建的，并且画出了像素。
    - macOS 上还销毁原生窗口：它的 Metal 图层一直留着画过的帧（IOSurface，30–50 MB），只有销毁才释放。
      窗口显示时 Qt 重新创建原生窗口，又按有标题栏的外框创建；`TitleBar` 像第一次那样隐藏标题，并还原窗口原来的
      位置和大小（最大化、全屏由 Qt 还原）。Windows 保留原生窗口，`WindowsFrame` 绑在它上面。
    - 页面各在一个 `PageLoader` 里，`active` 跟随 Main.qml 的 `holdsPages`。`present()` 在显示窗口前同步创建
      七个页面，第一帧就有内容；登录时隐藏启动的窗口第一次显示时才创建。`PageLoader` 不用 `Loader`：`Loader` 是
      焦点域，Tab、Shift+Tab 经它进入页面时丢掉焦点原因，代理页靠这个原因决定落到哪一行；它的异步加载也会改变
      页面里对象完成的先后，如策略组菜单用 `Instantiator` 插入的类型项会排到后面。侧栏、托盘、「未能断开连接」
      对话框和授权引导不卸下。
    - 回来时当前页（`ShellViewModel`）和 ViewModel 里的状态不变，如代理页选中的组、各页的搜索、排序和筛选；分流页
      的标签和「从链接导入」表单由 Main.qml 的 `kept` 在卸下前记下、装上后还原，隐藏期间导入完成就不还原表单。
      滚动位置和键盘焦点不保留。窗口上还开着对话框或菜单时不卸页面，只释放图形资源，留着用户没做完的编辑。
    - 隐藏期间托盘菜单、「未能断开连接」对话框、授权引导和一键导入链接照常：它们先 `present()`，页面随之装上。
      订阅更新、自动备份等定时任务在 ViewModel 里，不受影响；网状网络的状态只在分流页显示时查询，卸下后不再查。
    - 实测（macOS，`scripts/preview.py` 的模拟宿主）：先显示再隐藏从约 232 MB 降到 135–160 MB，登录时隐藏启动从
      145 MB 降到 103 MB，显示中不变（250–280 MB）；释放后再显示 `present()` 约 100 ms、首帧 120–160 ms，原来
      10 ms 左右。多次显示隐藏后停在 160 MB 左右，多出的是 malloc 没还给系统的空闲页，活对象各轮之间不增长。
      Windows 没有实测。
      `tests/test_hidden_window.py` 检查卸下、还原和隐藏期间的对话框与链接。
- **退出 Solaris**（D41）：托盘菜单的「退出 Solaris」、macOS 的 ⌘Q 和 Dock 的「退出」、关闭动作为 `quit` 时关闭
  窗口，都调用 `DesktopViewModel.quit`。它先经 `OverviewViewModel.disconnect_to_quit` 断开本用户的隧道：发的是与
  「断开」相同的 `Stop`，服务据此清除 `was_running`、取消睡眠唤醒恢复（architecture.md §5.2），概览页的开关照常显示
  正在断开；隧道断开后才离开事件循环。macOS 上窗口隐藏时没有 Dock 图标和菜单，退出入口是菜单栏的「退出 Solaris」；
  窗口显示时 ⌘Q 和 Dock 的「退出」照旧。
  - 不需要断开时直接退出：上一次刷新显示隧道已停止且没有在恢复，或隧道属于别的用户（D5b，不是本用户可以断开的），
    或刷新得到 `service_unavailable`（服务没在运行，也就没有隧道）。还没刷新过、刷新因别的原因失败时照样发 `Stop`。
  - 断开最多等 10 秒（`QUIT_DISCONNECT_TIMEOUT_S`）。失败或超时时显示窗口和「未能断开连接」对话框：原因，加上
    「仍然退出时，隧道可能仍在运行」；「重试」（默认）再断开一次，「仍然退出」直接离开，Esc 取消退出、留在应用里。
    断开期间再次要求退出不重复断开；对话框开着时再次要求退出只把窗口带到前面。
  - Qt 把 ⌘Q、Dock 的「退出」和系统在注销、重启、关机时的请求都报告为 `Quit` 事件；Qt 6 处理它时会先逐个关闭窗口，
    有窗口拒绝就放弃退出，而主窗口在 `tray` 时正是拒绝关闭、改为隐藏，所以 `app.Application` 自己处理这个事件：
    用户的请求交给 `DesktopViewModel.quit`，系统的请求直接离开事件循环，不断开（Windows 由服务按 D5 在属主注销时
    停止并在下次登录时恢复）。`desktop/session.py` 区分两者：macOS 上只有系统发来的退出 Apple Event 带
    `kAEQuitReason`；其他平台上 `Quit` 事件只来自系统（Windows 的 `WM_ENDSESSION`），托盘的退出不经过它。
    离开事件循环用 `exit`，不用 `quit`：macOS 上 `quit` 会经 `terminate:` 再变成一个 `Quit` 事件。
  - 应用内更新的下载和校验在界面进程里，退出时随之取消，不拖住退出。Windows 安装器用 `--quit` 关闭界面，不断开，
    升级后服务按 `was_running` 恢复隧道（architecture.md 第 9 节）；macOS 的磁盘映像由用户自己退出 Solaris（照常
    断开），替换后手动连接。
  - 界面崩溃或被结束时没有机会断开，隧道仍由服务或系统扩展维持（architecture.md §1）。
- **单实例**：第一个界面在用户数据目录持有 `ui.lock`（`QLockFile`，进程不在了的锁视为过期），并以
  `solaris-<用户名和数据目录的哈希>` 为名监听 `QLocalServer`（只允许同一用户连接）。后来的界面拿不到
  锁，就连上这个名字发一行 `activate`，然后退出；第一个界面收到后显示并激活窗口。后来的界面是带着一键导入
  链接启动的（Windows 上链接是命令行参数），就改发 `open <链接>`，第一个界面显示窗口并预填导入表单
  （release.md 第 7 节）；一行超过 16 KiB 的消息被丢弃。Windows 上后来的界面先调用
  `AllowSetForegroundWindow`，让第一个界面能把窗口带到前台。由锁而不是由服务端名字来判断，是因为 macOS 上
  崩溃的界面会留下 socket 文件，而且两个同时启动的界面拿锁时只有一个能成功。
- **`--quit`**：`Solaris.exe --quit`（macOS 上是 `Solaris.app/Contents/MacOS/Solaris --quit`）不启动界面，只请
  正在运行的界面退出：拿不到锁时发一行 `quit`，第一个界面收到后不断开隧道，直接离开事件循环（D41）；
  然后每 100 毫秒试一次锁，拿到（界面已关闭数据库并放开锁，或进程已经不在）就放开并以 0 退出，10 秒还拿不到
  以 1 退出。没有界面在运行时立即以 0 退出，不创建任何文件。它只能请到同一用户、同一数据目录的界面。Windows
  安装器和卸载器用它关闭界面（architecture.md 第 9 节）。
- **备份与同步**：设置页左栏的一个分组，由 `viewmodels/backup.py` 的 `BackupViewModel` 提供（storage.md 第 3 到 6 节）。
  备份不加密，但界面不为此单独提示（D30）：导出、开启 iCloud、登录 Google Drive 都不弹确认框，说明里也不写。分组里有：
  - 「导出到文件」：「导出…」直接打开系统的保存对话框，默认文件名 `solaris-backup-<时间>.json`。平时只有标题；导出后
    第二行显示「已导出到 …」或失败原因，6 秒后消失。
  - 「从文件恢复」：「恢复…」打开系统的打开对话框。本机没有配置时直接按"替换"导入；有配置时弹出「从文件恢复」
    对话框：备份的设备、时间和配置、方案的份数，本机现有的份数，以及"合并"（默认）和"替换"两个选项，「恢复」是
    默认按钮。恢复期间两行的按钮不可用，说明处显示「正在恢复，并重新下载订阅…」；结束后显示「已从 … 恢复」，
    有配置更新失败时显示「已恢复；N 份订阅更新失败，已在订阅页标出」，隧道没能切到恢复后的分流时说明原因。这一句
    一直留到下一次恢复。不是备份文件、版本更新的备份、读不了的文件都在这里说明，数据不动。
  - 「iCloud」（只在 macOS 上显示）：第二行写「未开启」「上次备份：5 分钟前 · 成功」「尚未备份」，失败时用 `warning`
    写「上次备份失败：储存空间已满」这样的原因；进行中写「正在备份…」等。右侧是「更多」按钮（菜单里只有破坏性的
    「删除云端备份」，确认框「删除 iCloud 中的备份？」）和开关。打开开关立即开启并备份一次；关掉开关不删云端的快照。
    系统没登录 iCloud 时第二行用 `warning` 写「系统未登录 iCloud」，开关换成「打开系统设置」（Apple 账户那一页）；
    iCloud 受限、暂时不可用时也写明。从源码运行或不签名的构建写「仅签名的安装版可用」，开关不可用。
  - 「Google Drive」（构建带 Google OAuth 客户端时显示，两个平台都有，storage.md 第 4 节）：没登录时第二行写「未登录」，
    右侧是「登录…」。点它在浏览器里打开 Google 的授权页，第二行写「请在浏览器中完成登录…」，按钮换成「取消」；浏览器
    回来后立即开启并备份一次。登录后第二行是账户和上次备份，如「user@gmail.com · 上次备份：5 分钟前 · 成功」，失败时
    同 iCloud 用 `warning` 写原因；右侧是「更多」按钮，菜单里是「退出登录」，分隔线下是破坏性的「删除云端备份」
    （确认框「删除 Google Drive 中的备份？」）。「退出登录」不再确认：关掉目标、撤销授权、删除本机的令牌，云端的快照
    保留。Google 拒绝了保存的登录时，第二行用 `warning` 写「user@gmail.com · 需要重新登录」，「登录…」重新出现，菜单
    里只有「退出登录」可用。登录失败、拒绝授权写「无法登录：没有获得授权」这样的原因，6 秒后恢复。
  - 分组下方（有云端一行时）是「立即备份」和「从云端恢复…」两个紧凑按钮，有开启且账户可用的目标时才可用，对所有
    这样的目标执行，进度和结果写在各自那一行。「立即备份」不比较摘要，总是上传；本机还没有数据时说明「本机还没有
    可备份的数据」。「从云端恢复…」列出这些目标的快照，弹出「从云端恢复」对话框（640px）：「备份」下是快照列表
    （单选，最新的在前，每行是时间、设备和配置、方案的份数，右侧是提供方 iCloud 或 Google Drive，最多显示 5 行，
    多了在框内滚动）；本机有配置时下面再有"合并"（默认）和"替换"，没有时直接替换。读不出快照的提供方在自己那一行
    说明原因，其余照常列出。恢复的进度和结果写在快照所在的那一行，6 秒后恢复原来的说明。
  - 恢复后 `BackupViewModel.restored` 通知订阅页、分流页、概览页和代理页重新读取，设置存储 `reload()`，语言和外观
    随之切换。
  - 自动备份的定时器也在 `BackupViewModel` 里，见 storage.md 第 3 节。
- **关于**：设置页的「关于」分组依次是版本（检查更新与下载）、自动检查更新、检查频率、内核、许可证、开源许可、
  诊断信息和崩溃报告。更新、开源许可、诊断和崩溃报告由 `viewmodels/about.py` 的 `AboutViewModel`
  提供，设计见 [release.md](release.md)；内核版本和许可证来自 `SettingsViewModel`。
  - 「检查频率」只在开启自动检查更新时显示，关闭时整行隐藏。
  - 「内核」只写版本号。内核报告的是 go.mod 里 replace 的版本，固定到提交时是 Go 伪版本（D38），如
    `v1.19.33-0.20261103155416-c2de0f865c7e`；`models/format.version_number` 去掉时间戳、提交哈希和 `+` 后的
    构建信息，写作 `v1.19.33`，侧栏的服务状态块相同。诊断信息的 `host.json` 保留完整版本。
- **登录时打开**（`desktop/login.py`，平台接口 `LoginItem`）：
  - Windows：为当前用户注册一个计划任务（`schtasks /Create /XML`）：登录触发器只针对该用户，以该用户身份、
    交互式令牌、不提权运行，普通优先级，没有运行时限，动作是 `<界面>.exe --background`（界面只进托盘）。
    任务名 `Solaris (<域>_<用户名>)`。用计划任务而不是注册表的 `Run` 键：登录后立即启动，不受 Windows
    对启动项的延迟；卸载器按同一个任务名清理（`package/windows/setup.ps1`）。读状态用
    `schtasks /Query /XML`，用户在任务计划程序里禁用的任务视为关闭。
  - macOS：`SMAppService.mainAppService` 的 `register` / `unregister`（macOS 13 起），经 ctypes 调用
    Objective-C 运行时，不依赖 PyObjC。状态为 `requiresApproval` 时设置页提示到系统设置的登录项里允许，并给出
    「打开系统设置」链接。登录项启动时不带 `--background`，界面会显示窗口。
  - 只有打包后的程序（Nuitka 编译，macOS 上还要在 `.app` 里）才注册；从源码运行时是 `UnavailableLoginItem`，
    开关不可用并注明「仅安装版可用」，避免在开发机上把 Python 解释器注册成登录项。测试只用替身，真实注册在
    测试机上验收（maintenance.md 2.1、2.2 节）。

## 9. 分流

设计见 [routing.md](routing.md)；这里写界面怎样实现它。

- **分流方式决定隧道运行什么**（`ProfileLibrary`）：
  - "订阅自带"（`routing_state.mode = 'profile'`）：当前配置（`ProfileList.active`，即 `routing_state.profile_id`）的
    原文，覆写为全局覆写加这份订阅自己的（routing.md §5.6）；
  - "我的方案"（`'plan'`）：`TunnelConfig.composition`，节点池里各订阅按列表顺序的原文、正在使用的方案
    （`plans.plan_json`，`locale` 跟随界面语言）和信息节点表达式（`routing_state.info_pattern`，为 NULL 时用
    `DEFAULT_INFO_PATTERN`）；覆写只有全局覆写。
  - Start、Reload、改 TUN 和端口时的重载都按当前的分流方式拼出配置。
- **导入与更新**：先 `inspect_source`。订阅带规则时再用自己的原文 `validate`（它要能单独使用）；只有节点的订阅
  （包括分享链接）不单独校验。分流方式是"我的方案"且这份订阅加入节点池时，还要用加上它之后的拼装校验。
  `proxy_count` 取 `inspect_source` 的 `node_count`：不含信息节点和组网出站，与节点池的算法相同。读出来的
  `SourceInspection` 缓存在内存里，界面启动后对还没读过的订阅补读一次（`inspect_all`），用于规则数的标注。
  是否带规则还和原文一起存在 `has_rules`，所以重启后、隧道断开时也知道哪些只有节点，"只有节点"的标注和挑选
  接替的订阅都用它。没有内核能读时存为未读，连接时再读（第 3 节）；未读的订阅不进拼装，是否带规则按未知处理。
  连接读完、隧道运行后，订阅页和分流页重新读取（概览看到隧道起停时，`RoutingViewModel.tunnel_running`）。
- **默认值**（routing.md §5.1）：
  - `mode` 为 NULL 只表示还没导入过订阅。第一次导入时，带规则就是"订阅自带"并使用这份订阅；只有节点就是
    "我的方案"，按模板生成方案（`plans.default_plan`，名为"日常"）。之后的导入不改分流方式；当前配置还空着时，
    第一份带规则的订阅成为当前配置。
  - 删除规则正在使用的订阅：改用列表里最早的另一份带规则的订阅，一份都没有就切到"我的方案"（没有方案时按
    模板生成）。隧道在运行时先用接替者重载，重载失败就不删除。
- **生效**（routing.md §5.4）：方案的每次改动都是"先 `validate`、再存储、正在使用时 `reload`"，在配置库的同一把
  锁下进行。改动的结果是 `CompositionReport`：警告按条目 id 显示在规则行上，地区组和节点数供目标下拉框、策略组
  标签和代理页使用。报告随节点池、方案和信息节点表达式的变化失效，分流页显示时重新拼装一次。没有内核能回答时
  （macOS 隧道未连接）改动不经校验直接存储，报告留空，连接时由 `Start` 校验，被拒绝就断开并报错。
- **没有内核时隐藏的部分**：`RoutingViewModel.coreAvailable` 取自 `TunnelHost.core_available`（经
  `ProfileLibrary.core_available`），概览看到隧道起停时更新；Windows 上恒为真，什么都不隐藏。为假时：规则测试框
  禁用，占位写「连接后可测试域名或 IP」；策略组编辑器的预览和挑选节点的列表只写「连接后显示」；不显示
  "以此创建我的方案"，方案菜单里的"导入…"、"导出…"和"查看生成的配置…"（`ConvertToPlan`、`ExportPlan`）。
  节点数和地区来自报告，没有报告时本来就不写。分类列表照旧用安装包的清单。
- **`RoutingViewModel`**（`viewmodels/routing.py`）：
  - 它管分流方式、方案的选择与增删、撤销，以及改动的提交。页面的各部分是它的子对象，读显示中的方案，改动交给它
    提交："订阅自带"是 `ProfileRoutes`（`viewmodels/routing_profile.py`）；规则标签是 `PlanRules`
    （`viewmodels/routing_rules.py`），含规则测试、搜索和按目标筛选；策略组标签上，各组是 `PlanGroups`
    （`viewmodels/routing_groups.py`），组网是 `MeshNetworks`，链式出口是 `WarpExit`、`VpnGateExit`；对话框是
    `RuleEditor`、`GroupEditor` 和 `PlanTransfer`。
  - QML 也这样拆：`ui/pages/RoutingPage.qml` 是页面外壳（标题栏、方案栏、标签切换和几部分共用的对话框），各部分
    在 `ui/pages/routing/`。
  - 改动立即显示，再交给配置库；被拒绝时恢复为已存储的方案，原因写在标题栏。
  - 拖动和菜单里的上移、下移先只改显示，停止 1 秒后作为一次改动提交；其他改动会顺带提交它们。
  - 删除规则后标题栏显示撤销提示 8 秒，或者到下一次改动为止；撤销就是把删除前的方案存回去（软删除的记录恢复）。
  - 添加规则对话框是 `RuleEditor`（`viewmodels/rule_editor.py`）：服务、分类、域名和 IP 段四种；多选的服务
    和分类各生成一条，域名和 IP 段一次生成一条；新条目插在最前面，编辑保持位置和 id。内核拒绝时对话框不关闭。
    分类列表取自 `list_geo_categories`，连不上时用安装包附带的清单。
  - "订阅自带"且这份订阅正在运行时，用 `list_groups` 和 `list_rules` 只读列出它的策略组和规则；不在运行时
    写出 `inspect_source` 给的数量。
  - **规则测试**：测试框的文字由 `plans.match_target` 读成主机和端口，停顿 0.4 秒或回车后交给
    `ProfileLibrary.plans.match_rule`：用显示的方案（含未提交的移动）、节点池和全局覆写拼成 `TunnelConfig`，调用宿主的
    `match_rule`。结果的条目 id 标出命中的行（`RuleRow.hit`，兜底是 `fallbackHit`），方案每次改动生效后用同一段
    文字重测。
  - **搜索与按目标筛选**只影响显示：`RuleRow` 仍按完整列表算 `first` / `last`，所以菜单里的上移、下移照常；
    `filtering` 为真时 QML 禁用拖动手柄。
  - **自建策略组**：`PlanGroups.custom` 列出方案里的自建组，说明写"类型 · 节点数、其他成员数、直连 / 拒绝"，节点数取
    报告里的 `node_count`。编辑器是 `GroupEditor`（`viewmodels/routing_groups.py`）：条件改动停止 0.25 秒后经
    `ProfileLibrary.plans.preview_group` 调用 `PreviewGroup`（组名临时用 id，名称不影响预览），延迟取自运行中内核的
    `list_groups`，按设置「延迟测试」取 HTTP 或 TCP 的结果。保存走 `PlanGroups.put_group`：编辑的组保持位置，新组排在最后，内核拒绝时对话框不关闭。
    挑选节点的列表是 `GroupEditor.nodes`（`NodeRow`，带 `category` 供 `PickList` 分节），取自报告的节点池；挑选的
    节点按（订阅 id，原名）与报告的 `PoolNode.source_id`、`original_name` 对上，对不上的按订阅是否还在、是否加入
    节点池给出失效原因（`_picks`），`PlanGroups` 用同一段给自建组行写 `warning`。没有报告时不判断失效。
    删除前列出引用它的规则条目、兜底和其他组（routing.md §5.5），确认后这些规则改走节点选择、其他组去掉它，
    可以撤销。
  - **转换与导出**是 `PlanTransfer`（`viewmodels/routing_transfer.py`）：`convert_profile` / `convert_file`
    调用 `ConvertToPlan`，摘要对话框确认后用 `plans.plan_from_json` 把内核写的方案 JSON 读成 `Plan`（条目的
    `items` 还原成界面的 `content`：域名加 `=`、`~` 前缀，IP 段带 `no_resolve`，分类的 GEOIP 没有 `no-resolve`
    时记 `"no_resolve": false`），再由 `ProfileLibrary.plans.add_plan` 校验、存储，勾选"创建后改用"时切到"我的方案"
    并使用它。导出和"查看生成的配置"调用 `export_plan`（`EXPORT_SHARE` / `EXPORT_GENERATED`，后者带运行中的或
    设置里的 `RunOptions`），只读显示；复制进剪贴板，另存为写 UTF-8 文件。
- **目标下拉框**（规则对话框和"其他流量"，`routing_text.target_options`）：节点选择、自动选择 → 有节点的地区 →
  自建组 → 组网出站 → WARP 出口、VPN Gate 出口 → 直连、拒绝，段之间有分隔线，空段省略。方案没有打开的出口
  禁用并注明"未启用"。规则行按报告的警告写目标后的提示：`region_empty`"无节点，暂走节点选择"、`group_empty`
  "没有成员，暂走节点选择"、`egress_unavailable`"不可用，已拒绝连接"、`mesh_missing`"不存在，已拒绝连接"；
  `via_empty`、`mesh_restricted` 不属于哪条规则，写在出口行和组网行上。
- **组网出站**（mesh.md 第 5 节）：策略组标签里自建之后是"组网"一节，由 `MeshNetworks`
  （`viewmodels/routing_mesh.py`）提供。行来自报告的 `mesh`（只在"我的方案"下），说明写"类型 · 来自「订阅名」"，
  有 `mesh_restricted` 时加"· 已关闭 选项"。应用内添加的网络排在它们前面，见 mesh.md 10.7 节；所以"我的方案"下
  这一节总是显示，最后一行是"添加网络…"。
  名称取报告里生成配置用的名称，报告里没有了（`mesh_missing`）时取 `mesh:` 引用里的原名。"更多"菜单：
  - "添加常用规则"：Tailscale 把 `plans.common_mesh_rules` 的 IP 段规则（`100.64.0.0/10`、`fd7a:115c:a1e0::/48`，
    `no-resolve`）和域名规则（`ts.net`）插在最前面，已有一样的条目就跳过，标题栏给出撤销提示；ZeroTier、EasyTier
    打开添加规则对话框的"IP 段"，目标已选好（`RuleEditor.openAddRanges`）。
  - "使用它的规则"：把目标筛选设为这个出站，切到规则标签。
- **链式出口**（exits.md 第 6 节）：组网之后是"链式出口"一节，WARP 和 VPN Gate 两行，由 `WarpExit`、
  `VpnGateExit`（`viewmodels/routing_exits.py`）提供。WARP 一行：说明写"经由 组名 · 协议"，经由组没有成员（`via_empty`）时写"组名，暂经
  节点选择"；右侧是状态："未启用"、"正在注册…"、"已就绪"、"未注册"、"连接后注册"，注册失败时为 `warning` 色的
  "不可用"（原因在对话框的账户一栏）。点行或菜单的"设置…"打开出口设置对话框（`WarpDialog.qml`）：
  - 开关、经由（策略组下拉框）、协议（三选一）都立即生效，没有保存按钮（DESIGN.md"开关"）：开关和经由改的是
    方案（`plans.egress`，与其他改动一样先校验再存储），协议改的是本机设置 `warp_protocol`。
  - 打开开关时还没有账户就注册；有账户时改协议就为它换密钥。"注册""重新注册"按钮在账户一栏，重新注册是换一个
    新设备。删除账户在账户一栏的"更多"菜单里，先确认，只删本机保存的副本。
  - 注册经 `ProfileLibrary.exits.register_warp`：经由组按报告里的组名传给宿主（没有成员时传节点选择），网络请求不占
    配置库的锁；账户存进凭据库后，在用的方案重新拼装并重载。隧道没在运行（或在用"订阅自带"）时记下这次请求，
    状态写"连接后注册"；概览页看到隧道开始运行时（`RoutingViewModel.tunnel_running`）补做，协议在断开时改过的也在
    这时换密钥。这个请求只在界面进程的内存里，退出界面就不再等。
  - 账户是凭据，存系统凭据库（`models/credentials.py`，D14）：`KeyringCredentials` 经 `keyring` 直接用平台的后端
    （macOS 钥匙串、Windows 凭据管理器，不靠入口点发现），`WarpAccounts` 把账户写成一条紧凑的 JSON（远小于
    Windows 一条凭据 2560 字节的上限）。它不进数据库，也不进备份；读不出来（如钥匙串锁着）时按没有账户处理，
    WARP 的规则拒绝连接。`ProfileLibrary` 拼装时只给打开了 WARP 出口的方案带上账户（`Composition.egress`）。
    测试把 `credentials.system_keyring` 换成内存里的实现（`tests/conftest.py`），不碰真实的凭据库。
  - **VPN Gate 一行**：说明写"经由 组名 · 国家"（几个国家用顿号隔开）；状态是"未启用"、"正在获取…"、在用的服务器
    （"日本 5 台"，几个国家时只写"5 台"）、"连接后获取"，没有在用的服务器时为 `warning` 色的"不可用"。"更多"菜单
    是"设置…"和"立即刷新"。对话框（`VpnGateDialog.qml`，宽版）开头一句写明只换 IP、不保护隐私、志愿者能看到访问的
    网站并保留两周日志、请勿用于大流量下载和视频；下面是开关、经由、服务器数量（1 到 10 台）、国家（下拉框，菜单里
    勾选在用的国家，点一项加上或去掉，带服务器数，至少留一个；复选框只用于提交时生效的表单，所以不用它们；选了
    几个国家时提示失效后可能换到别国的服务器）、分配方式（按网站分配 / 只用一台），都立即改方案；最后是服务器一栏：在用几台、列表更新于何时（或失败的原因）、"立即刷新"，以及在用
    的服务器（国家 · 主机名、运营者、最近一次延迟），超过五台时在框里滚动。
  - 列表经 `ProfileLibrary.exits.refresh_vpngate` 获取：与注册 WARP 一样经由组按报告里的组名传给宿主，网络请求不占配置库
    的锁，隧道没在运行时记下请求，状态写"连接后获取"，连接后补做。列表、在用的服务器（按组里的顺序）和一小时内
    失效的服务器存在本机 `state` 表的 `vpngate` 一行（`models/vpngate.py`），不进备份。`vpngate.pick` 在方案的国家
    里按 Score 取前 N 台：仍可用的服务器留在原位，空出的位置按分数补上。拼装时只给打开了出口的方案带上服务器
    （`Composition.egress`）；在用的方案拼装时，挑出的服务器成为新的"原位"。
  - 隧道运行时每两分钟一次（`VpnGateExit` 的计时器）：列表超过一小时就重新获取；否则 `check_vpngate` 读
    `list_groups` 里这些服务器最近一次 URL 测试的延迟（`delay_ms`），失败（延迟 0）的记一小时并让出位置，挑出的服务器变了就重载；能用的
    不到一半、且上次获取已过十分钟时重新获取列表。
- **其他页面**：订阅页的行在说明里写"规则使用中""不加入节点池"；概览页"出站"分组的"当前使用"跳到分流页；
  代理页用报告里的节点池标出每个节点的来源订阅，地区组收进可折叠的"地区"小节；连接页用
  `Connection.rule_entry_id` 显示规则条目名称，第二行是内核的原始规则。
- **服务清单与地区名称**：`models/services.py`（41 个服务，九个类别）和 `models/regions.py`（与内核的地区表
  同序，名称用 `qsTr` 的英文译文）。服务的中英文名称在清单里，按当前安装的翻译选用（`i18n.current_language`）。
  `tests/test_services.py` 核对清单引用的分类都在附带的 GEO 数据里，地区表与 `regions.go` 一致。

