# 数据存储与云备份

本文描述界面侧用户数据的持久化方式（SQLite），以及如何把这些数据备份到 Google Drive 和 iCloud。
相关决策是 [roadmap.md](roadmap.md) 中的 D13 和 D14，实施计划见 roadmap 的 M7。

状态：设计已定，尚未实现。目前的实现仍是 [architecture.md](architecture.md) §7.2 的文件存储。

## 1. 范围

- **存储内容**：配置（订阅和本地文件，以及是否加入节点池）、覆写（全局一份，每个配置各一份）、分流方式与分流方案
  （策略组和规则，见 [routing.md](routing.md)）、设置。
- **不在本文范围内**：
  - Windows 服务在 `C:\ProgramData\<Product>\` 下保存的运行副本（architecture.md §5.1），它只用于开机恢复；
  - mihomo 自己的 provider 缓存和 `cache.db`。
- **读写方**：数据库只由界面进程以当前用户身份读写。服务和系统扩展都不访问它，它们从宿主请求里拿到配置。

## 2. 本地数据库

- **位置**：用户数据目录下的 `mihomo-desktop.db`。macOS 为 `~/Library/Application Support/<Product>/`，Windows 为 `%LOCALAPPDATA%\<Product>\`。
- **实现**：使用标准库 `sqlite3`，开启 WAL 模式。schema 版本记在 `PRAGMA user_version` 里，启动时按版本依次迁移。
- **写入**：每次写入都在一个事务里完成。界面是单实例，所以同一时间只有一个进程打开数据库。
- **主键**：所有记录都用 UUID 作主键，并记 `updated_at`；删除采用软删除，记 `deleted_at`。备份恢复时靠这几项按记录合并；将来如果改成多设备双向同步，也不需要改表结构。

表结构（草案）：

| 表 | 内容 |
| --- | --- |
| `profiles` | `id`、`name`、`kind`（`subscription` / `file`）、`in_pool`（是否加入节点池）、`url`、`auto_update_hours`、`position`、`created_at`、`updated_at`、`deleted_at` |
| `profile_contents` | `profile_id`、配置 YAML 原文、`fetched_at`、用量（upload / download / total / expire）、`proxy_count`。这是本机缓存，订阅下载的内容不进入备份 |
| `overrides` | `scope`（`global` 或某个 `profile_id`）、YAML 原文、`updated_at`、`deleted_at` |
| `routing_state`、`plans`、`plan_groups`、`plan_rules` | 分流方式、节点池设置、分流方案及其策略组和规则条目，字段见 routing.md 第 9 节 |
| `settings` | `key`、`value`（JSON）、`scope`（`synced` / `local`）、`updated_at`。每个设置项属于哪个 scope，由设置页的定义决定 |
| `state` | 只属于本机的状态，例如当前配置 |
| `backup_targets` | 已开启的备份目标：提供方、显示用的账户名、上次成功备份的时间、上次的错误。不存令牌（见 4 节） |

**迁移**：首次启动新版本时，如果发现 M2 的文件存储（`profiles.json`、`profiles/*.yaml`、`override.yaml`、
`overrides/*.yaml`）或临时的设置文件，就把它们导入数据库。导入后，旧文件改名加上 `.migrated` 后缀保留，不删除。

## 3. 备份快照

D13 定下的方式是**备份与恢复**，不做多设备双向同步：

- 本机数据有变化时，自动上传一份快照；
- 新设备登录同一个账户后，可以一键恢复；
- 几台设备同时修改时，以最后上传的那份快照为准。

**快照内容**：一个 JSON 文档，包含以下字段：

- `format`（固定为 `"mihomo-desktop-backup"`）、`version`、`created_at`、`device`（设备名）、`app_version`；
- `profiles`：配置列表。订阅只存链接和更新设置，不存下载到的内容；本地文件配置存 YAML 原文；
- `overrides`：全部覆写；
- `routing`：分流方式、节点池设置和全部分流方案（包括策略组和规则条目）；
- `settings`：只包含 `synced` 范围的设置；
- 当前使用的配置或方案。

快照不包含用量、缓存、本机范围的设置，也不包含任何令牌。

**不加密**（D13）：快照里的订阅链接是明文，安全性完全依赖 Google 或 Apple 账户本身。开启备份时，界面要明确告知用户
"备份中包含订阅链接，任何能访问该云端账户的人都能看到"。

**上传时机**：

- 数据变化后去抖 30 秒再上传；
- 启动时，如果距上次成功备份已超过 24 小时，也上传一次；
- 只有快照内容的哈希变了才上传。

**保留份数**：每个备份目标在云端保留最近 10 份快照，用来找回误删的数据。

**恢复**：

1. 本机没有任何配置时，直接导入快照。
2. 本机已有数据时，让用户选择"替换"或"合并"。合并按 `id` 进行，`updated_at` 较新的一方胜出。
3. 恢复完成后，重新下载所有订阅，并逐条调用宿主的 `validate`；校验失败的配置会被标出来，不会被丢弃。

## 4. Google Drive

- **登录**：在应用内登录（D13），采用 OAuth 2.0 的已安装应用流程。在系统浏览器里打开授权页，回调到
  `127.0.0.1` 上的一个随机端口（loopback），并使用 PKCE。
- **权限**：只申请 `https://www.googleapis.com/auth/drive.appdata`。快照存放在 Drive 的应用专属隐藏目录
  （appDataFolder）里，在用户的 Drive 中看不到；应用也读不到用户的其他文件。
- **网络请求**：用标准库 `urllib` 调 token 端点和 Drive v3 的 files 接口，不引入 Google SDK。所有请求
  都在界面进程里、以用户身份发出，跟随系统代理设置。
- **令牌**：refresh token 存在系统的凭据库里（macOS 钥匙串、Windows 凭据管理器），不存进数据库。具体怎么实现见待决策的 D14。
- **退出登录**：撤销令牌，并删除本机保存的令牌。云端的快照保留不动；界面另外提供"删除云端备份"。
- **前置条件**：
  - 在 Google Cloud 建一个项目，配置 OAuth 同意屏幕，并创建一个"桌面应用"类型的 OAuth 客户端；
  - 正式发布前，按 Google 的要求完成同意屏幕的审核。

  桌面应用的 client secret 本来就无法保密，可以随源码一起发布。

## 5. iCloud（仅 macOS）

- **方式**：使用 CloudKit 的私有数据库（D13）。数据占用的是用户自己的 iCloud 空间，开发者看不到。
- **账户**：第三方应用不能自己登录 iCloud，只能使用系统已登录的 Apple ID。系统没有登录 iCloud 时，界面提示用户
  去系统设置登录。
- **依赖条件**：需要 Developer ID 签名、iCloud（CloudKit）entitlement、container（`iCloud.<bundle id>`）以及对应的
  provisioning profile。因此这部分依赖 M5 建立的 Xcode 工程和签名流程，也依赖 D1 产品名（它决定 bundle id）。
- **实现**：在 M5 的管理库（Swift，导出 C ABI）里加几个函数，分别用于保存、列出、下载和删除快照，Python 通过
  ctypes 调用。每份快照存成一条记录，快照内容放在 `CKAsset` 里。
- **Windows**：不提供 iCloud 备份，只能用 Google Drive。

## 6. 提供方接口

- `BackupProvider` 接口包含 `sign_in()`、`sign_out()`、`account()`、`upload(snapshot)`、`list()`、
  `download(id)` 和 `delete(id)`。
- Google Drive 和 CloudKit 各有一个实现。测试用内存实现和本地目录实现，不访问真实的云服务。
- 可以同时开启多个备份目标，例如在 macOS 上同时用 iCloud 和 Google Drive，每个目标各自上传、各自保留快照。
- 设置页新增"备份与同步"分组，内容包括：已登录的账户、上次备份的时间和结果、立即备份、从云端恢复、退出登录、
  删除云端备份。
