# 数据存储与云备份

本文描述界面侧用户数据的持久化方式（SQLite），以及如何把这些数据备份到文件、iCloud 和 Google Drive。相关决策是
[decisions.md](decisions.md) 中的 D13、D14 和 D30。存储层的实现细节（线程、迁移）见 [ui.md](ui.md) §2，界面见 ui.md §8。

## 1. 范围

- **存储内容**：配置（订阅和本地文件，以及是否加入节点池）、覆写（全局一份，每个配置各一份）、分流方式与分流方案
  （策略组和规则，见 [routing.md](routing.md)）、应用内添加的组网网络、设置。
- **不在本文范围内**：
  - Windows 服务在 `C:\ProgramData\Solaris\` 下保存的运行副本（architecture.md §5.1），它只用于开机恢复；
  - mihomo 自己的 provider 缓存和 `cache.db`；
  - 链式出口的 WARP 账户（系统凭据库）、VPN Gate 服务器列表（`state` 表）和 Tor 数据目录，都只在本机，不进备份
    （[exits.md](exits.md) 第 8 节）；
  - 应用内组网网络的凭据（系统凭据库），不进备份；网络本身进备份（[mesh.md](mesh.md) 10.2 节）。
- **读写方**：数据库只由界面进程以当前用户身份读写。服务和系统扩展都不访问它，它们从宿主请求里拿到配置。

## 2. 本地数据库

- **位置**：用户数据目录下的 `solaris.db`。macOS 为 `~/Library/Application Support/Solaris/`，Windows 为 `%LOCALAPPDATA%\Solaris\`。
- **实现**：使用标准库 `sqlite3`，开启 WAL 模式。schema 版本记在 `PRAGMA user_version` 里，启动时按版本依次迁移。
- **写入**：每次写入都在一个事务里完成。界面是单实例，所以同一时间只有一个进程打开数据库。
- **主键**：配置、方案、规则条目、组网网络和备份目标用 UUID 作主键；覆写、设置和本机状态以 `scope`、`key` 这样的
  自然键为主键，这样两台设备上的"全局覆写"或同一个设置项是同一条记录；策略组的 `id` 只在所属方案内唯一（地区组的
  `id` 由地区代码派生，routing.md 第 9 节）。每行都记 `updated_at`；可以删除的记录采用软删除，记 `deleted_at`。
  备份恢复时靠这几项按记录合并；将来如果改成多设备双向同步，也不需要改表结构。
- **约束**：表都是 `STRICT`；`in_pool`、`unread`、`hidden`、`enabled` 只能是 0 或 1，`has_rules` 另可为 NULL；
  `plan_groups`、`plan_rules` 以外键指向 `plans`，方案被清除（见下）时一并删除。种类、方式这类取值由代码检查，不写成
  `CHECK`，以后加一种取值不必重建表。
- **墓碑的保留**：软删除的记录（墓碑）保留 90 天（`database.TOMBSTONE_DAYS`）。每次启动时 `store.open_database`
  在单实例锁之内删掉 `deleted_at` 早于 90 天前的记录，涉及 `profiles`、`overrides`、`plans`、`plan_groups`、
  `plan_rules`、`mesh_networks` 和 `backup_targets`；被清除的方案连同它的策略组和规则一起删除。这样删掉的订阅链接、
  覆写原文不会一直留在本机，快照里的墓碑也不会越积越多。清除改变快照内容，下一次自动备份会上传新的一份。
  `backup_targets` 不进快照，它的墓碑只为和其他表一致。
- **清除对合并的影响**：合并靠墓碑判断"本机删除得更晚"。墓碑清除后，本机分不出一条记录是"这里删过"还是"这里
  从来没有"，一律按后者处理，把快照里的记录当作新记录加回来：
  - 删除之前拍下的快照，在删除后 90 天之内合并，删除仍然生效；过了 90 天再合并，删掉的记录会回来，要再删一次；
  - 另一台设备的快照同理：那台设备在 90 天内没有合并到这次删除，它的快照里这条记录仍是活的，之后在这里合并时
    记录会回来；
  - 快照里的记录在删除之后又被修改过的，本来就该胜出（第 3 节），清除不改变这一点；"替换"也不受影响。

  反过来跳过这些记录是不行的：快照里修改时间早于清除界限、本机又没有的记录，也可能来自另一台设备、本机从来没有，
  而把另一台设备的数据并进来正是合并的用途。跳过会悄悄丢掉它们，加回来的代价只是再删一次。90 天远长于自动备份的
  间隔（变化后 30 秒，至少每天一次），只有隔了很久才合并的旧快照会碰到。

表结构：

| 表 | 内容 |
| --- | --- |
| `profiles` | `id`、`name`、`kind`（`subscription` / `file`）、`in_pool`（是否加入节点池）、`url`、`path`（本地文件配置的原路径）、`auto_update_hours`、`position`、`created_at`、`updated_at`、`deleted_at` |
| `profile_contents` | `profile_id`、配置 YAML 原文、`fetched_at`、用量（upload / download / total / expire）、`proxy_count`、`has_rules`（内核读出的是否带规则）、`unread` 和 `read_yaml`（存下时没有内核能读，连接时再读；内核拒绝时退回上次读过的原文，ui.md 第 3 节）、`updated_at`。这是本机缓存，订阅下载的内容不进入备份 |
| `overrides` | `scope`（`global` 或某个 `profile_id`）、YAML 原文、`updated_at`、`deleted_at` |
| `routing_state`、`plans`、`plan_groups`、`plan_rules` | 分流方式、当前使用的配置、节点池设置、分流方案及其策略组和规则条目，字段见 routing.md 第 9 节 |
| `settings` | `key`、`value`（JSON）、`updated_at`。每个设置项的范围（`synced` / `local`）由 `models/settings.py` 里字段的元数据决定（ui.md §8），不存进表里 |
| `state` | 只属于本机的状态（`key`、`value`（JSON）、`updated_at`）：每个备份目标上次上传的快照摘要（`backup_digest:<目标 id>`）；VPN Gate 出口的服务器列表、在用的服务器和近一小时失效的服务器（`vpngate`，exits.md 5.2 节） |
| `backup_targets` | 已开启的备份目标：`id`、提供方、显示用的账户名、上次成功备份的时间、上次的错误、`created_at`、`updated_at`、`deleted_at`。不存令牌（见 4 节） |
| `mesh_networks` | 应用内添加的组网网络：`id`、`kind`（`tailscale` / `zerotier` / `easytier`）、`name`、`via`、`settings`（JSON，不含凭据）、`position`、`created_at`、`updated_at`、`deleted_at`。凭据在系统凭据库（mesh.md 10.2 节） |

当前使用的配置存在 `routing_state` 里，作为"订阅自带"时用的配置（routing.md 第 9 节），不在 `state` 里。

**表结构的修改**：`database.py` 的 `MIGRATIONS` 是一串迁移函数，第 n 个把版本 n−1 升到 n（ui.md §2.3）；版本 1 是
1.0.0 的表结构。改表结构就在 `MIGRATIONS` 末尾追加一个迁移并配上测试，已有的迁移不再改动，用户已有的数据库都要能
升上来。存下来的格式同样要让以后的版本读得懂：配置、备份快照（第 3 节，格式变了就把 `version` 加一，旧的照样能
恢复）和方案 JSON。这条工作规则也写在 [maintenance.md](maintenance.md) 第 5 节。

## 3. 备份快照

D13 定下的方式是**备份与恢复**，不做多设备双向同步：

- 本机数据有变化时，自动上传一份快照；
- 新设备登录同一个账户后，可以一键恢复；
- 几台设备同时修改时，以最后上传的那份快照为准。

**快照内容**：一个 UTF-8 的 JSON 文档（`models/backup.py`），包含以下字段：

- `format`（固定为 `"solaris-backup"`）、`version`（目前为 1）、`created_at`（Unix 秒）、`device`（设备名，取主机名并去掉
  `.local`）、`app_version`；
- `profiles`：配置列表，按列表顺序。每条有 `id`、`name`、`kind`、`url`、`path`、`auto_update_hours`、`in_pool`、
  `position`、`created_at`、`updated_at`。订阅只存链接和更新设置，不存下载到的内容；本地文件配置另有
  `config`（`yaml` 原文和它的 `updated_at`，即 `profile_contents.updated_at`）；
- `overrides`：全部覆写，`scope`、`yaml`、`updated_at`；
- `routing`：`mode`、`profile_id`（当前使用的配置）、`plan_id`（当前使用的方案）、`info_pattern`（信息节点表达式）、
  `updated_at`，以及 `plans`：全部分流方案，每套带 `groups` 和 `rules`，字段与表相同，`egress`、`conditions`、
  `options`、`content` 是 JSON 值而不是字符串；
- `mesh`：应用内添加的组网网络，字段与表相同，`settings` 是 JSON 值；
- `settings`：只包含 `synced` 范围、改过的设置，`key`、`value`、`updated_at`。

快照不包含用量、下载缓存（`profile_contents` 里订阅的原文和下载信息）、本机范围的设置、`state` 和 `backup_targets`，
也不包含任何令牌、WARP 账户和组网网络的凭据。恢复出来的网络要重新登录或重新填写密钥（mesh.md 10.2 节）。

- **删除的记录**：配置、覆写、方案、策略组、规则条目和组网网络删除后只留**墓碑**：主键、`updated_at` 和 `deleted_at`，
  不带内容，所以删掉的订阅链接不会一直留在备份里。删除的方案不带它的策略组和规则。墓碑在本机保留 90 天
  （第 2 节），清除之后快照里也就没有了。
- **摘要**：去掉 `format`、`version`、`created_at`、`device`、`app_version` 之后，按键排序的紧凑 JSON 的 SHA-256。
  同样的数据不论何时、在哪台设备上导出，摘要都相同；订阅更新、用量变化、本机设置的改动都不改变摘要。
- **读取**：超过 64 MiB、不是 JSON、`format` 不对或字段类型不对的文件报 `not_a_backup`（或 `too_large`）；`version`
  比本程序认识的新报 `newer_backup`。所有记录都在写入数据库之前检查过。

**不加密**（D13）：快照里的订阅链接和本地文件配置的 YAML 原文（其中可能有组网出站的凭据，[mesh.md](mesh.md)
第 7 节）都是明文，安全性依赖保管文件的人和云端账户（Apple 账户、Google 账户）本身。界面不为此单独提示：导出、开启
iCloud、登录 Google Drive 都不弹确认框（D30）；[PRIVACY.md](../PRIVACY.md) 写明备份不加密。

**上传时机**：

- 数据变化后去抖 30 秒再上传；
- 启动时和之后每小时，如果距上次成功备份已超过 24 小时，或者上次上传失败了，也上传一次。上次失败的目标只在这时
  重试，不随数据变化重试：没网、空间满、没登录时不会每隔半分钟试一次；
- 只有快照内容的哈希变了才上传；
- 什么数据都还没有的数据库（新装、还没恢复的机器）不上传：它只会把旧机器最新的快照往淘汰的方向挤。

**保留份数**：每个备份目标在云端保留最近 10 份快照，用来找回误删的数据。

这几条由 `models/backup_auto.py` 的 `AutoBackup` 实现，在提供方之上，对每个目标分别执行：数据变化时调用
`changed()`，记下 30 秒后到期；定时调用 `run_due()`，到期才上传，期间再有变化就顺延，上次失败的目标跳过；启动时
和之后每小时调用 `catch_up()`，给上次成功备份超过 24 小时（或从未成功）、或者上次失败的目标上传。上传前比较快照摘要和这个目标上次
收下的摘要（`state` 里的 `backup_digest:<目标 id>`），相同就跳过（"立即备份"不比较）；成功后记下摘要和时间，再删掉
第 10 份之后更旧的快照（删除失败不影响这次上传，下次再删）。失败时把原因（`代码: 说明`）记进
`backup_targets.last_error`，不记摘要，下一次 `catch_up()` 重试，也不影响其他目标。`BackupTargets` 读写
`backup_targets` 表，关掉的目标标记删除；上传途中被关掉的目标什么也不记（`record()` 返回 None，不写摘要，也不清理
旧快照）。删除了一个目标的全部云端快照后，`forget()` 清掉它的摘要和上次备份时间，开着的目标在下一次数据变化或
下一个小时的 `catch_up()` 时重新上传。提供方此刻用不了的目标原样等着，不尝试、不记失败：这个构建没有这个提供方
（例如从源码运行），iCloud 的系统账户不可用（没登录、受限、暂时不可用），或者 Google Drive 没有保存的登录（第 4 节）。

接到界面上的是 `BackupViewModel`（有云端提供方时才运行）：

- **数据变化**：每 5 秒看一次数据库连接的 `total_changes`（SQLite 记的这个连接插入、修改、删除的累计行数）。所有
  写入都走这一个连接，所以不论哪个页面、哪条路径改了数据都看得到，不必在每个写入点各发通知。备份自己的写入
  （每次上传的结果、开关和删除时对 `backup_targets` 的改动）经 `Changes.own()` 执行，执行期间一直占着连接，写下的
  行数从计数里扣掉，不算变化。订阅更新、本机设置也算变化，但它们只会多算一次摘要，不会多传一份。
- **一次一件**：自动备份、开关、立即备份、删除云端备份在 asyncio 线程上依次执行（`asyncio.Lock`）。上传途中关掉
  开关或删除云端备份，会等这次上传结束再做，所以删除时连它传上去的那份一起删掉，关掉的目标也不会留下摘要。
- **启动**：先 `catch_up()`，再当作数据刚变过：上次运行在去抖的 30 秒内退出时，最后的修改还没传上去，这样 30 秒后
  比一次摘要就补上了。
- 每分钟刷新一次"上次备份"的相对时间；系统没登录 iCloud 时，顺便重新读账户状态，用户在系统设置里登录后不用重开：
  读到账户重新可用时，下一次检查就 `catch_up()`。读到 iCloud 的账户状态和 Google Drive 有没有登录之前，不做自动备份。

**导出到文件**：设置页可以把快照导出成文件，再从文件恢复，不需要云端账户。文件就是上面的 JSON 文档，同样不加密；
「导出…」直接打开系统的保存对话框。

**恢复**：

1. 本机没有任何配置时，直接导入快照（按"替换"）。
2. 本机已有数据时，让用户选择"替换"或"合并"。合并按 `id` 进行，`updated_at` 较新的一方胜出。
3. 恢复完成后，重新下载所有订阅，并逐条调用宿主的 `validate`；校验失败的配置会被标出来，不会被丢弃。

实现（`Backups.restore`，一个事务）：

- **替换**：本机变成和快照一样。本机没有的记录按快照原样插入，保留它的时间；本机有、内容不同或已删除的记录改成
  快照的内容，`updated_at` 记为现在；快照里没有（或是墓碑）的本机记录标记删除，时间也是现在。`synced` 范围的设置
  取快照里的值，快照里没有的恢复默认值；`routing_state` 取快照的值。这样新设备上恢复出来的数据和原设备的摘要相同，
  而在已有数据的设备上，"替换"本身算作一次最新的修改。
- **合并**：每条记录单独比较：本机没有，或者快照里的 `updated_at` 更新，就取快照的（连同它的时间）；墓碑更新时
  把本机记录标记删除，本机没有这条记录时什么也不做。策略组按（`plan_id`，`id`）、规则条目按 `id`，各自比较；
  合并出的规则指向已删除的策略组时，内核把它交给"节点选择"并在报告里说明（routing.md 8.1 节）。本地文件配置
  的原文是单独的一条记录，按它自己的 `updated_at` 比较。`synced` 设置逐项比较，`routing_state` 整行比较。
  两台设备互相合并对方的快照之后，数据相同。
- 两种方式都只接受本版本认识的 `synced` 设置和它接受的值；本地设置不动。不再存在的配置的下载缓存一并删除。
- 写入由 `ProfileLibrary.profiles.restore(write)` 在它的锁里执行，所以订阅的自动更新不会拿旧的列表覆盖恢复的数据。写完后
  它重新读取配置、分流和方案，再逐份检查：订阅重新下载，本地文件配置用快照里的原文，和导入时一样经
  `inspect_source`，带规则的再经 `validate`，"我的方案"节点池里的还要和方案一起拼装校验。失败的配置保留，记为
  "更新失败"，在订阅页标出；还没下载到原文的订阅不给节点池提供节点。没有内核能读时（macOS 隧道未连接）原文存为
  未读，连接时再读。当前配置不在了时，按第一份带规则的配置接替。
  最后，隧道在运行就按恢复后的分流重新加载；重新加载失败时，数据仍是恢复后的，界面说明隧道没有切换。设置存储
  随后 `reload()`，各页重新读取。

## 4. Google Drive

两个平台都有，前提是构建带有 Google OAuth 客户端（见下面"OAuth 客户端"）；没有客户端时设置页没有 Google Drive 一行。
macOS 上可以和 iCloud 同时开启，各自上传、各自保留快照。实现是 `models/backup_google.py` 的 `GoogleDriveProvider`。

- **登录**：在应用内登录（D13），采用 OAuth 2.0 的已安装应用流程。界面进程在 `127.0.0.1` 的一个随机端口上监听，用
  系统浏览器打开 Google 的授权页，`redirect_uri` 是 `http://127.0.0.1:<端口>`；带 PKCE（S256）和随机的 `state`，
  并以 `access_type=offline`、`prompt=consent` 让每次登录都拿到 refresh token。监听只接受带这个 `state` 的
  `GET /`，给浏览器回一页"已登录，可以关闭这个页面"，其他请求回 404。最多等 10 分钟，界面上可以取消；取消、超时或
  出错时监听随即关闭。拒绝授权或没有勾选 Drive 的权限报 `not_authorized`。拿到令牌后用 Drive 的 `about.get` 读
  账户的邮箱，作为备份目标的账户名。
- **权限**：只申请 `https://www.googleapis.com/auth/drive.appdata`。快照存放在 Drive 的应用专属隐藏目录
  （appDataFolder）里，在用户的 Drive 中看不到；应用也读不到用户的其他文件。Google 把这个范围列为非敏感。
- **令牌**（D14）：refresh token 连同账户名以紧凑 JSON 存在系统凭据库（macOS 钥匙串、Windows 凭据管理器，键
  `google-drive`），用 `keyring` 读写，不进数据库、备份和诊断信息。access token 只在内存里，过期前 60 秒或 Drive
  回 401 时用 refresh token 换新的。凭据库读不出来（如钥匙串锁着）时按没有登录处理，下次再读。Google 拒绝
  refresh token（`invalid_grant`：授权被撤销、令牌失效）时，本机删除它；目标保留，那一行写"需要重新登录"，自动备份
  等到重新登录，不会每小时失败一次。
- **网络请求**：用标准库 `urllib` 调 token、revoke 端点和 Drive v3 的 files、about 接口，不引入 Google SDK。所有请求
  都在界面进程里、以当前用户身份发出，跟随系统代理设置，和订阅下载一样。每个请求在自己的 daemon 线程上执行，超时
  60 秒，进行中的请求不拖住退出。
- **快照**：每份快照是 appDataFolder 里的一个文件，名字与目录提供方相同（`solaris-backup-<UTC 时间>-<摘要前 12 位>.json`），
  以 multipart 上传。`SnapshotInfo`（时间、设备名、版本、配置和方案的份数）放在文件的 `appProperties` 里，列出时只取
  `id`、`name`、`size`、`appProperties`，不下载快照；Drive 每个属性的键和值合计最多 124 字节，设备名过长时按字符
  截断。列出时逐页读取，只认这样命名、属性读得出来的文件。条目的 id 是 Drive 的文件 id；下载用 `alt=media`，删除
  不存在的文件不报错。保留份数与其他目标相同（10 份，第 3 节）。
- **错误**：Drive 的 `storageQuotaExceeded` 报 `cloud_full`；连不上、超时、429、5xx 和限流报 `cloud_unreachable`；
  换过令牌后仍是 401、令牌被拒报 `not_signed_in`；没有这个文件报 `backup_missing`；其余报 `cloud_failed`，带 Google
  的说明。
- **退出登录**：先关掉目标，再向 `https://oauth2.googleapis.com/revoke` 撤销 refresh token，然后删除本机保存的
  令牌。云端的快照保留不动；界面另外提供"删除云端备份"。Google 撤销的是这个项目得到的整个授权，所以同一个 Google
  账户在其他设备上的登录也随之失效，那些设备下次上传时写"需要重新登录"。撤销请求发不出去（如没网）时照样删除本机
  的令牌，授权可以在 Google 账户的"第三方应用和服务"里移除。
- **换账户**：目标"需要重新登录"时，用另一个 Google 账户登录，新目标换掉原来的（连同它的摘要），并立即备份一次。
- **OAuth 客户端**：Google Cloud 项目里一个"桌面应用"类型的客户端。它的 ID 和 secret 不进仓库：
  - 从源码运行时读环境变量 `SOLARIS_GOOGLE_CLIENT_ID`、`SOLARIS_GOOGLE_CLIENT_SECRET`，两个都要有；
  - 打包时 `scripts/build_app.py`、`scripts/build_windows.py` 读同样的环境变量，写成 `build/<平台>/google_client.json`，
    由 Nuitka 放到包旁边的 `solaris/google_client.json`。只设了一个时构建失败；都没设时照常构建，只是没有 Google
    Drive；
  - 发布工作流从 `release` Environment 的同名机密传给两个构建 job（[release.md](release.md) 1.1 节）。

  已安装应用的 client secret 无法保密，Google 也不把它当作机密，打包的应用里它是明文。在 Google Cloud 里要做的
  设置见 [maintenance.md](maintenance.md) 2.8 节。
- **审核**：`drive.appdata` 是非敏感范围，不需要 Google 的应用审核。只有想在授权页显示应用的名称和图标时，才要做
  较轻的品牌审核（brand verification：经过验证的域名上的主页和隐私政策）。同意屏幕要发布为"正式版"（In production）：
  测试状态下只有列出的测试用户能登录，他们的授权 7 天后失效。

## 5. iCloud（仅 macOS）

- **状态**：已完成接入并正式启用。用户可在「设置 → 备份与同步 → iCloud」中主动开启，使用系统已登录的 Apple 账户备份与恢复快照；不做双向实时同步。
- **方式**：使用 CloudKit 的私有数据库（D13）。数据占用的是用户自己的 iCloud 空间，开发者看不到。
- **账户**：第三方应用不能自己登录 iCloud，只能使用系统已登录的 Apple 账户。系统没有登录 iCloud 时，界面提示用户
  去系统设置登录。CloudKit 不告诉应用账户名，所以备份目标的账户名就写"iCloud"；换了 Apple 账户之后，快照进的是
  新账户的私有数据库。
- **依赖条件**：需要 Developer ID 签名、iCloud（CloudKit）entitlement、container（`iCloud.<bundle id>`）以及对应的
  provisioning profile，因此需使用带相应授权的 macOS 签名安装版；container 是
  `iCloud.dev.tschen.solaris`，环境是 Production（Developer ID 只能用它），签名的细节见
  [apple/README.md](../apple/README.md)「iCloud 备份」。CloudKit schema 的部署维护要求，以及后续版本的真实账户回归清单，
  见 [maintenance.md](maintenance.md) 2.3 节。
- **管理库**（`apple/Manager/CloudBackup.swift`）：`solaris_manager_icloud_status`、`_save`、`_list`、`_download`、
  `_delete` 五个函数，和其他函数一样阻塞到完成或超时，在后台线程调用。每份快照是默认 zone 里一条 `Snapshot` 类型的
  记录：记录名由 Python 给出，快照内容放在 `CKAsset`（字段 `snapshot`），`SnapshotInfo` 和大小作为 JSON 放在
  字符串字段 `info` 里，列出时只取 `info`，不下载快照。保存用 `.allKeys`，同名记录直接覆盖。CloudKit 的错误映射成
  结果码：没登录（`NOT_SIGNED_IN`）、没有这条记录（`NOT_FOUND`）、空间已满（`QUOTA_EXCEEDED`）、网络或服务暂时
  不可用（`UNREACHABLE`），其余是 `FAILED` 加 CloudKit 的说明。没有 iCloud entitlement 的构建（不签名、ad hoc）里
  CloudKit 会抛出结束进程的 Objective-C 异常，所以管理库先读自己进程的 entitlement，没有这个 container 就不创建
  `CKContainer`：状态报 `UNSUPPORTED`，其余调用报 `FAILED`。
- **Python**（`models/backup_icloud.py` 的 `ICloudProvider`）：经 `ManagerLibrary`（ctypes）调用，每次调用在一个
  daemon 线程里执行，进行中的上传不拖住退出。记录名与目录提供方的文件名相同，只是没有 `.json`；`list()` 只认这样
  命名、`info` 读得出来的记录。错误变成 `BackupError`：`not_signed_in`、`backup_missing`、`cloud_full`、
  `cloud_unreachable`、`cloud_failed`。`sign_in()` 只检查系统账户可用，`sign_out()` 什么也不做。打包的 app 里
  （`Contents/Frameworks` 有管理库）才有这个提供方；从源码运行时没有，设置页写「仅签名的安装版可用」。
- **Windows**：不提供 iCloud 备份。Windows 上的云端备份是 Google Drive（第 4 节）。

## 6. 提供方接口

- `BackupProvider` 接口（`models/backup_providers.py`）包含 `sign_in()`、`sign_out()`、`account()`、`upload(snapshot)`、
  `list()`、`download(id)` 和 `delete(id)`，都是协程，由各实现决定放到哪个线程上执行。`kind` 是存进
  `backup_targets.provider` 的名字。`list()` 返回 `BackupEntry`（`id`、`info`、`size`），按时间从新到旧；`info` 是
  `SnapshotInfo`：时间、设备名、版本和配置、方案的份数，供"从云端恢复"的列表显示。`download` 不认识的 id 报
  `backup_missing`，`delete` 删除已经不在的快照不报错。提供方只在应用自己的空间里存快照（CloudKit 容器、Drive 的
  appDataFolder、一个目录），所以列出的都是本应用的快照。
- Google Drive 的实现见第 4 节，CloudKit 的见第 5 节。另有两个不访问云服务的实现：
  - `MemoryProvider`：快照放在内存里，测试用；可以模拟退出登录和失败；
  - `DirectoryProvider`：快照存成目录里的文件，`solaris-backup-<UTC 时间>-<摘要前 12 位>.json`，原子写入。只列出
    这样命名、能读成快照的文件，别的文件不碰；id 只接受这种文件名，到不了目录之外。
- 可以同时开启多个备份目标，每个目标各自上传、各自保留快照；macOS 上可以同时用 iCloud 和 Google Drive。
- 设置页"备份与同步"分组：导出到文件、从文件恢复；macOS 上有 iCloud 一行（开关、上次备份的时间和结果、删除云端
  备份）；构建带 Google OAuth 客户端时有 Google Drive 一行（登录、账户、上次备份的时间和结果、删除云端备份、退出
  登录）。分组下方是对所有目标的"立即备份"和"从云端恢复"（ui.md §8）。
