# 维护

Solaris 从 1.0.0 起进入维护。本文是日常工作的依据：各平台的现状（第 1 节）、要在真机上做的验收（第 2 节）、已知
问题（第 3 节）、发布步骤（第 4 节）和工作规则（第 5 节）。决策见 [decisions.md](decisions.md)，各部分的设计见
[development.md](development.md#文档) 的文档一览。

## 1. 现状

除 1.4 节列出的以外，功能都已实现；但服务、系统扩展、安装包和链式出口只在 CI、替身和本机的假服务器上验证过。
真机上要做的验收在第 2 节，做完一项就从本节和第 2 节删掉相应的条目。

### 1.1 CI 覆盖

`.github/workflows/ci.yml` 在每个 PR 和 main 上运行：

| job | 平台 | 内容 |
| --- | --- | --- |
| `python` | macOS、Windows | ruff、pyright、pytest（离屏）；macOS 上还核对生成的协议、主题和翻译是最新的 |
| `e2e` | macOS | 界面连真实的开发服务（`tests/e2e`）：导入、连接、各页数据、分流与方案、重载不断开连接；节点是本机起的代理，只走回环地址 |
| `go` | macOS、Windows、Linux | `go vet`、`go test`，再以 `-race` 跑一遍；Linux 上以 `with_gvisor` 带 `-race` 再跑安全项和组网测试，交叉编译 Windows 服务；macOS 上编扩展用的 C archive |
| `apple` | macOS | 管理库和系统扩展的不签名构建，再用检查用的 app 做一次发布用的磁盘映像（`scripts/build_dmg.py`） |
| `windows-installer` | Windows | 构建不签名的安装包，在 runner 上安装、打开界面、升级、卸载（`package/windows/smoke-test.ps1`） |
| `tun` | Linux | 在网络命名空间里建真实的 TUN：自管模式和 fd 模式，四种栈，fd 模式另测 `includeAllNetworks` |

Windows 上的客户端校验（命名管道、文件 ID、锁住映像、服务的可执行文件只在有管理员权限时通过）、管理员权限的判断、
home 目录的 ACL 和 WinINet 代理设置的读写在 `go` job 的 Windows runner 上测；界面以 `runas` 启动程序、取回退出码在
`python` job 的 Windows runner 上测（runner 本来就提权，不弹 UAC 提示）。服务的属主逻辑（认领、换属主重启、管理员
接管、注销停止、登录恢复、系统代理的开启与还原）用替身平台在所有平台上测。

### 1.2 Windows：没在真机上运行过的

- 服务以 SYSTEM 身份运行时的认领与重启辅助进程、`SessionLogon` / `SessionLogoff`、开机后登录时恢复；
- 管理员接管（D5b）：UAC 的同意提示、提权的接管辅助进程通过客户端校验、两个登录用户之间的接管；
- 模拟属主 token 写系统代理，浏览器随之生效；
- 真实 ProgramData 下的 ACL，Nuitka 构建的界面通过客户端校验；
- wintun、strict-route、四种 TUN 栈和 DNS 泄漏；Windows 的默认栈还没定（设置页默认"跟随配置"）；
- UAC 提示、升级后恢复隧道；
- 界面只在 CI 的离屏环境里运行过：渲染、自绘顶部区域的窗口按钮、拖动和贴靠、读屏软件都没在 Windows 上看过。

### 1.3 macOS：没在真机上运行过的

- 签名和公证：发布工作流还没运行过，公证结果未知；
- 系统扩展的激活与批准，hardened runtime 下的启动，登录项（`SMAppService`）；
- 扩展找 utun 描述符、四种栈经过 utun 的流量、DNS 劫持、重连时描述符的归属；macOS 的默认栈还没定；
- `includeAllNetworks`（D19），provider message 的大小上限（D8）；
- 隧道断开时导入订阅、连接后再读（D27）；
- iCloud 备份：CloudKit 从没真正运行过，Production 的 schema 也还没部署（2.3 节）。

两个平台都没做过的：真实订阅（e2e 的节点是本机起的代理）、WARP 与 VPN Gate 出口和组网（只对假服务器、构造的出站和
模拟宿主测过）、"检查更新"对真实 Release（只对本机的假服务器测过）、Google Drive 备份（只对本机的假 OAuth 和 Drive
服务器测过；Google Cloud 的项目和 OAuth 客户端还没建，2.8 节）。

### 1.4 没有实现的

- Tor 出口（D23，[exits.md](exits.md) 第 4 节）：只有设计；
- Windows 代码签名（D29）：签名方案定下后接上 `product.WindowsSigner`、`SignedRelease` 和界面的
  `signature.WINDOWS_SIGNER`（[release.md](release.md) 第 3 节）；
- Windows 服务处理 PowerEvent（唤醒后重置连接），以及界面、服务、内核三层逐层递减的超时
  （[architecture.md](architecture.md) §5.2）；
- macOS 卸载前停用系统扩展（`deactivationRequest`），扩展说明文字的本地化；
- 崩溃日志。

## 2. 真机验收

需要的环境：

| 环境 | 用于 |
| --- | --- |
| 一台全新的 Windows 10 / 11 x64 虚拟机 | 2.1、2.4 |
| 一台单独的 Apple Silicon 测试 Mac 或 macOS 虚拟机，装 macOS 26 | 2.2、2.3、2.4 |
| 一个测试用的 Apple 账户 | 2.3 |
| 一个测试用的 Google 账户，以及 Google Cloud 里的项目和 OAuth 客户端 | 2.8 |
| 一个只有 TCP 的真实节点（另有一个支持 UDP 的更好） | 2.5、2.6 |
| 自己的 Tailscale、ZeroTier 或 EasyTier 网络 | 2.7 |

2.1 到 2.4 要建 TUN、激活系统扩展或改系统代理，只在测试机上做（5.1 节）。2.5 到 2.7 用开发服务做，不建 TUN：
`uv run python scripts/dev.py --data ~/solaris-dev`，经混合端口访问。脚本按发布版本的标签 `with_gvisor` 构建开发服务，
不带它 mihomo 会拒绝 Tailscale 出站。

### 2.1 Windows

在全新的 Windows 虚拟机上依次完成，并且 DNS 泄漏测试通过：

1. 安装后连接；
2. 关闭界面，隧道仍在运行；
3. 重启系统，隧道自动恢复；
4. 升级；
5. 卸载后不留服务和运行文件。

同时核对：

- 安装器的 UAC 提示（发布者显示"未知"），SmartScreen 的提示与 README 写的一致；
- 认领：用第二个用户打开界面，属主的隧道在运行时它看到"其他用户正在使用"；隧道停了以后它能认领，服务经辅助进程
  重启；
- 注销属主时隧道停止，再登录后恢复；开机后先登录的其他用户不走属主的隧道（D5）；
- 管理员接管（D5b）。准备一个标准用户和一个管理员（UAC 保持默认），标准用户是属主，开着"系统代理"连接：
  1. 用快速切换用户登录管理员，打开界面：概览页标题栏写"其他用户正在使用 · 另一位用户已连接"，右侧有"接管"。
     再登录一个标准用户，他看到的是"……对方断开后才能在这里连接"，没有"接管"；
  2. 管理员点"接管"：确认框默认聚焦"取消"。确认后弹出 UAC 的同意提示（程序 `solaris-service.exe`，发布者"未知"），
     不要求密码。选"否"：什么都不变，也不报错；
  3. 再点一次，在 UAC 里选"是"：服务重启，管理员的概览页变成"未连接"，可以连接；`service.log` 里有
     `an administrator took over the tunnel`，`owner.json` 是管理员的 SID；
  4. 切回原属主：他的系统代理已写回原来的设置，浏览器不再经代理；管理员连接后他的界面写"其他用户正在使用"；
     管理员断开后他能再连接，他的隧道不会自己恢复；
  5. 在管理员的普通（不提权）命令提示符里运行 `"C:\Program Files\Solaris\solaris-service.exe" -take-over`：退出码
     非 0，`service.log` 里是 `rejected control client` 和"without an administrator's rights"，隧道不受影响；
- 系统代理：开着"系统代理"连接后浏览器经代理；断开、注销、停止服务时写回原来的设置；
- `C:\ProgramData\Solaris` 和 `users\<SID>` 的 ACL；复制到别处的 `Solaris.exe` 连不上服务；
- wintun、strict-route，设置页"TUN 协议栈"的四种栈都能连上并转发流量；据此定下 Windows 的默认栈；
- 升级后隧道按 `was_running` 恢复；
- 界面：浅色与深色、125% / 150% 缩放；自绘顶部区域的窗口按钮、拖动、双击最大化、贴靠布局；只用键盘走完各页；用
  讲述人读一遍主要页面。

### 2.2 macOS

在全新的 Mac 上：安装公证后的 .app → 批准系统扩展 → 连接 → 退出 App 后隧道仍在 → 重新打开 App 能读到状态。

同时核对：

- **Gatekeeper**：用浏览器从 GitHub 下载发布工作流产出的 dmg（这样会带上隔离标记），打开映像，把 Solaris 拖进
  "应用程序"后第一次打开，系统只应显示"从互联网下载"的普通确认，不应拒绝打开（发布工作流里 Gatekeeper 的检查只报
  警告，见第 3 节）；
- 激活时的批准与 VPN 配置的允许；系统要求重启时的提示；
- app 在 hardened runtime 下启动，"登录时打开"（`SMAppService`）；
- 扩展找到 utun 描述符；四种栈经过 utun 的流量、DNS 劫持（198.18.0.2）；断开与重连后描述符的归属；据此定下 macOS
  的默认栈；
- `includeAllNetworks`（D19）：打开后系统默认绕开隧道的流量也进入隧道，局域网流量不进；扩展自己（mihomo 的出站）
  的流量不被卷回隧道；`mips` 和 `gvisor` 栈都能工作；保存配置时系统是否再次请用户允许；
- provider message 的大小上限（D8）：用一份大配置 `Reload`，记下能带多大；配置不分片，真带不动时回到 D8 重新决定；
- 隧道断开时导入订阅，然后连接（D27）：未读的订阅在 `Start` 之前读取并校验，记下要多久；读不了时隧道断开。

### 2.3 iCloud 备份

先在 CloudKit Console 里建 schema（一次）。Production 环境不能由应用自动建 schema，所以 record type 要先在
Development 里建好再部署：

1. 打开 [CloudKit Console](https://icloud.developer.apple.com)，选 container `iCloud.dev.tschen.solaris`，进
   Development 环境的 Schema → Record Types，新建 `Snapshot`，加两个字段：`info`（String）、`snapshot`（Asset）。
2. 同一 record type 的 Indexes 里给 `recordName` 加一个 Queryable 索引（列出快照的查询要用；不加会报
   "Field 'recordName' is not marked queryable"）。
3. Deploy Schema Changes，部署到 Production。Production 的 schema 之后只能加不能删。

没部署时，列出快照报"The iCloud container has no Snapshot record type; deploy its schema"，保存报 CloudKit 的
"Cannot create new type"，都会写在设置页 iCloud 一行。

然后用签名、公证过的 app，在测试 Mac 上登录测试用的 Apple 账户：

1. 设置页"备份与同步"有 iCloud 一行，写"未开启"。打开开关后不弹确认框（D30），写"上次备份：刚刚 · 成功"。CloudKit Console 的 Production → Records（用同一个 Apple 账户登录，Private
   Database，`Snapshot`）里有一条记录。
2. 改一个设置或导入一份订阅，约 35 秒后多一条记录；什么都不改时不再多。"立即备份"每次都多一条；超过 10 条后
   最旧的被删掉。
3. 另一台 Mac（或删掉 `~/Library/Application Support/Solaris` 后）打开开关：不上传空数据；"从云端恢复…"列出快照，
   恢复后订阅、方案、覆写、设置都回来，订阅重新下载并通过校验。
4. "更多"→"删除云端备份"后 Console 里记录全部消失，iCloud 一行写"尚未备份"。
5. 断网后"立即备份"：写"上次备份失败：无法连接，稍后会再试"；联网后一小时内（或下次启动）自动补上。
6. 在系统设置里退出 iCloud：一行写"系统未登录 iCloud"，"打开系统设置"打开 Apple 账户那一页；重新登录后一分钟
   内恢复。
7. `build_app.py --unsigned` 的 app：一行写"仅签名的安装版可用"，打开设置页不崩溃。
8. 账户的 iCloud 空间满时，失败原因是"储存空间已满"。

### 2.4 真实网络

在 2.1、2.2 的测试机上一起做：

- 用真实的订阅导入、更新、连接，"订阅自带"和"我的方案"各一次；
- DNS 泄漏；IPv6：有 IPv6 的网络上经隧道访问 IPv6 网站，关掉 `ipv6` 时 IPv6 流量被丢弃（D20）；
- 睡眠唤醒、切换网络（Wi-Fi 与有线、换一个 Wi-Fi）后连接恢复；
- 规则测试的结果在 TUN 和 fake-ip 下与实际连接一致（[routing.md](routing.md) 第 12 节）。

### 2.5 WARP 出口

经一个只有 TCP 的真实节点（设计见 [exits.md](exits.md) 第 3 节）：

1. 打开 WARP 出口，注册成功；
2. 添加"ifconfig.me → WARP 出口"，访问得到的是 Cloudflare 的 IP，而不是节点 IP；
3. 删除账户后，同一规则的连接被拒绝，不会经节点直接出去；
4. 协议切到 WireGuard（节点支持 UDP 时），结果相同。

做法：导入含这个节点的订阅，切到"我的方案"并连接，在节点选择里选中它；打开 WARP 出口完成注册；加一条
`ifconfig.me` 的域名规则，目标"WARP 出口"。然后经混合端口访问：`curl -x http://127.0.0.1:<混合端口>
https://ifconfig.me` 应是 Cloudflare 的地址，与节点直接出去的地址不同；`https://www.cloudflare.com/cdn-cgi/trace`
经同一条规则时应有 `warp=on`。删除账户后同一条命令应失败；节点支持 UDP 时把协议换成 WireGuard 再测一次。同时记下
exits.md 第 9 节的待实测项。

### 2.6 VPN Gate 出口

经一个真实节点（设计见 [exits.md](exits.md) 第 5 节）：

1. 打开 VPN Gate 出口，选日本，"ifconfig.me → VPN Gate 出口"返回日本的 IP，而不是节点 IP；
2. 按网站分配：几个不同的网站分到不同的服务器，同一网站多次访问的 IP 不变；
3. 让一台服务器连不上：两次连接失败、健康检查完成后，分到它的网站换到别的服务器，其他网站的 IP 不变；能用的
   不到一半时自动刷新列表；
4. 列表为空时，同一规则的连接被拒绝。

做法：导入含一个真实节点的订阅，切到"我的方案"并连接，在节点选择里选中它；在分流页"链式出口"打开 VPN Gate 出口，
国家只选日本，等状态变成"日本 N 台"（对话框的服务器一栏列出在用的服务器）；加一条 `ifconfig.me` 的域名规则，目标
"VPN Gate 出口"。然后经混合端口访问：`curl -x http://127.0.0.1:<混合端口> https://ifconfig.me` 应是一个日本的 IP，
与节点直接出去的地址不同，也不在节点的地区；`curl -x … https://ipinfo.io/json` 可看国家和运营商。

- 第 2 项：对几个不同的网站（例如 `ifconfig.me`、`api.ipify.org`、`icanhazip.com`，都加进同一条规则）各访问几次，
  比较返回的 IP；连接页看每条连接走的是哪台服务器。
- 第 3 项：在代理页的"VPN Gate 出口"组里看哪台服务器分到了 `ifconfig.me`，用本机防火墙或把那台的 IP 加一条
  `IP-CIDR,<IP>/32,REJECT` 覆写让它连不上；两分钟内对话框里它被换掉，`ifconfig.me` 换了 IP，其他网站不变。
- 第 4 项：换一个新的数据目录（`--data ~/solaris-dev2`），新建一个只含"拒绝"的自建组，把经由设成它再打开出口：列表
  取不到，对话框写出原因，规则行显示"不可用，已拒绝连接"，同一条命令失败。关掉出口时也一样。

同时记下 exits.md 第 9 节的待实测项。

### 2.7 组网

设计见 [mesh.md](mesh.md)。

**订阅带来的组网出站**（D24）：导入一份含 Tailscale 出站（测试 tailnet 的 auth key）的本地配置 → 它不在"自动选择"
和地区组里 → 用"添加常用规则"加规则后，能经它访问 tailnet 里的一台机器 → 删掉这份配置后，同一规则的连接被拒绝。

**应用内组网**（mesh.md 第 10 节）：

1. Tailscale，浏览器登录：添加 → 浏览器打开登录页 → 登录后行变成"在线"，地址是 `100.x` → "添加常用规则" →
   经混合端口访问 tailnet 里一台机器的 `100.x` 地址和 MagicDNS 名称 → tailnet 管理页里出现这台设备。
2. Tailscale，授权密钥：同上，不经浏览器；再改经由为"节点选择"，确认仍能访问（只走 DERP 中继）。
3. ZeroTier：添加网络 ID → 行上显示本机 ID、"等待授权" → 在 my.zerotier.com 授权 → "在线"，地址是分配的地址
   → 添加 IP 段规则后能访问网络里的设备。
4. EasyTier：两台机器，同网络名称、密钥，开发服务这边写对端的 `tcp://` 地址 → "在线"、1 台设备 → 能访问对端。
   用 `lsof -i -P | grep solaris` 确认开发服务没有监听 EasyTier 端口。
5. 改一条规则（触发重新加载），经 Tailscale 的 SSH 会话不断；删除网络后它的规则拒绝连接，内核 home 下
   `mesh/<id>` 不见了。
6. 导出备份，确认文件里有网络、没有任何密钥；在另一台机器恢复后网络出现，Tailscale 要求重新登录。

同时记下 mesh.md 第 8 节的待实测项。

### 2.8 Google Drive 备份

设计见 [storage.md](storage.md) 第 4 节。先在 Google Cloud 建 OAuth 客户端（一次）：

1. 用打算作为应用所有者的 Google 账户打开 [Google Cloud Console](https://console.cloud.google.com)，新建一个项目
   （如 `Solaris`）。
2. "API 和服务" → "库"，搜索 Google Drive API，启用。
3. "Google Auth Platform"（旧称"OAuth 同意屏幕"）→ 开始配置：
   - 品牌：应用名称 `Solaris`，用户支持邮箱、开发者联系邮箱；
   - 受众群体：用户类型选"外部"（External）；
   - 数据访问：添加范围 `https://www.googleapis.com/auth/drive.appdata`，它在"非敏感范围"一栏，不要加别的范围；
   - 受众群体 → 发布状态：点"发布应用"，改成"正式版"（In production）。测试状态下只有列出的测试用户能登录，他们
     的授权 7 天后失效，自动备份会随之停下。
4. "客户端" → 创建客户端，应用类型选"桌面应用"（Desktop app），名称如 `Solaris desktop`。记下客户端 ID
   （`….apps.googleusercontent.com`）和客户端密钥（`GOCSPX-…`）。桌面应用不用登记重定向 URI，Google 接受
   `http://127.0.0.1` 上的任意端口。
5. GitHub 仓库 → Settings → Environments → `release` → Environment secrets，添加两个机密：
   `SOLARIS_GOOGLE_CLIENT_ID`（客户端 ID）、`SOLARIS_GOOGLE_CLIENT_SECRET`（客户端密钥）。两个都不进仓库。

审核：`drive.appdata` 是非敏感范围，发布为正式版不需要 Google 的应用审核，也没有用户数上限。不做品牌审核时，Google
的授权页不显示应用的名称和图标；要显示，需要经过验证的自有域名上的主页和隐私政策，在"品牌"里填好后提交品牌审核。

然后用一个测试用的 Google 账户，在测试机上用发布工作流的安装包验收；也可以在开发机上从源码试：设好两个环境变量
后运行 `uv run python scripts/preview.py`（模拟宿主，临时数据目录，不动本机网络），令牌存进本机真实的钥匙串，试完
"退出登录"即删除。

1. 设置页"备份与同步"有 Google Drive 一行，写"未登录"。不带这两个机密构建的安装包没有这一行。
2. "登录…"在浏览器里打开 Google 的授权页，只请求"查看、创建和删除其自身在 Google 云端硬盘中的配置数据"一项；
   允许后浏览器显示"已登录 Google Drive，可以关闭这个页面，回到 Solaris。"，这一行写"<邮箱> · 上次备份：刚刚 · 成功"。
   在授权页拒绝时写"无法登录：没有获得授权"；关掉标签页后点"取消"，回到"未登录"。
3. 令牌只在系统凭据库里：macOS"钥匙串访问"里有服务 `Solaris`、账户 `google-drive` 的一项，Windows 凭据管理器里
   有对应的普通凭据；`solaris.db`、导出的备份文件和诊断信息 zip 里都搜不到它。
4. drive.google.com → 设置 → 管理应用，Solaris 有"隐藏的应用数据"；云端硬盘里看不到快照文件。
5. 改一个设置，约 35 秒后多一份快照（"从云端恢复…"列表多一行）；什么都不改时不再多。"立即备份"每次都多一份；
   超过 10 份后最旧的被删掉。
6. 另一台机器（或换一个数据目录）用同一个账户登录：不上传空数据；"从云端恢复…"列出快照，右侧写 Google Drive，
   恢复后订阅、方案、覆写、设置都回来。macOS 上同时开着 iCloud 时，两个提供方各自上传，列表里两者都有。
7. "更多"→"删除云端备份"后列表为空，这一行写"<邮箱> · 尚未备份"。
8. 断网后"立即备份"：写"上次备份失败：无法连接，稍后会再试"；联网后一小时内（或下次启动）自动补上。设了系统代理
   时，请求经代理发出（看代理的日志）。
9. "更多"→"退出登录"：这一行写"未登录"，凭据库里的那一项不见了；Google 账户的"第三方应用和服务"里不再有
   Solaris；云端的快照还在（重新登录后能列出）。第 6 项的另一台机器在下次上传时写"<邮箱> · 需要重新登录"。
10. 在 Google 账户的"第三方应用和服务"里移除 Solaris：下次上传时这一行写"<邮箱> · 需要重新登录"，之后不再尝试；
    重新登录后立即备份一次。
11. 账户的存储空间满时，失败原因是"储存空间已满"。

## 3. 已知问题

- **Gatekeeper 对公证过的 app 有两种说法**：发布工作流里公证被接受、票据已 staple，同一个 app 下载到 Mac 上用
  `syspolicy_check distribution` 检查是"可以分发"，`spctl --assess --type execute` 却拒绝，syspolicyd 的日志里是十来个
  `errSecCSUnsigned`（被当成代码却没有签名的项目）和"没有匹配的规则"，路径被系统打码。怀疑是 Nuitka 放在
  `Contents/MacOS` 里、签名存在扩展属性里的数据文件（QML、字体等，三百多个），还没坐实。runner 上的
  `syspolicy_check` 另外报 "Internal Xprotect Error"，在 Mac 上不出现。所以发布工作流只把公证与 staple 当关卡，这两项
  检查只报警告；以测试 Mac 上下载后打开的结果为准（2.2 节）。要是那里被拒，就从这些数据文件查起，例如把它们移出
  `Contents/MacOS`。
- **mihomo 内部的数据竞争**：mihomo 用终结器关闭重载换下的出站，关闭时写日志要读日志级别，而下一次
  `ApplyConfig` 开头的 `log.SetLevel` 不加锁地写它；`ApplyConfig` 还先改写入站对每个连接都要读的局域网、免认证
  设置，再关闭入站。两处都在 mihomo 里，读写的是一个整数和几个切片。停止时 engine 先关入站、自己关出站，不经过
  这两处；重载时避不开。`-race` 构建在每次应用配置前等到期的终结器跑完（`internal/engine/race.go`），发布构建
  不等；入站的那处没法等，所以测试不要连上入站后紧接着重载。mihomo 的 `route.ReCreateServer` 在协程里关闭、替换
  包级的控制器 server，也不加锁；engine 清掉控制器地址，所以平时只读不写，只有故意直接起控制器的
  `TestSecurity3EmbedModeGuardsAStrayController` 会写，它在 `-race` 下跳过，普通的 `go test` 照跑。
- **mihomo 读延迟不加锁**：延迟历史的队列（`common/queue` 的 `Last`）先不加锁地读长度，再加锁取值；url-test、
  fallback、load-balance 组的后台 URL 测试写入时，读节点最近的延迟或 url-test 组此刻所用的节点（`ListGroups`、
  TCP 测速找组所用的节点，D32）会被 `-race` 报出，读的只是切片长度。测试先等这些组第一次测完
  （`tcpdelay_test.go` 的 `urlTested`），再读延迟。
- **GEO 数据的自动更新**在 engine 的协程里调用 mihomo 的 `UpdateGeoDatabases`，它和解析配置共用 mihomo 的包级
  状态，没有加锁（mihomo 自己的定时更新也是这样）。测试不跑真实的更新。
- **macOS 的登录项**由 `SMAppService` 不带参数启动 app，所以登录后界面会显示窗口；Windows 的计划任务带
  `--background`，只进托盘（[ui.md](ui.md) 第 8 节）。
- **WARP 的注册接口不是官方的**：Cloudflare 改动或限流时注册会失败。失败时拒绝连接，不退回普通代理；接口的常量
  集中在 `src/core/internal/warp`。

## 4. 发布

发布物的约定、发布工作流和检查更新的细节见 [release.md](release.md)。步骤：

1. **改版本**：`pyproject.toml` 的 `version`，三段数字（`X.Y.Z`）。`uv lock` 更新锁文件里的版本；macOS 构建的版本
   号由 `scripts/build_apple.py` 从 `pyproject.toml` 读取。合并到 main，CI 全绿。
2. **试运行**：在 Actions 里手动运行 Release 工作流（`workflow_dispatch`，选 main）。它在 `release` Environment
   里，审批后构建、签名、公证两个安装包，存为 workflow artifact，不建 Release。下载 artifact，在测试 Mac 和
   Windows 虚拟机上装一遍；macOS 的磁盘映像打开后应与 `package/dmg/preview.png` 一致。Environment 里没有
   Google OAuth 客户端的两个机密（2.8 节）时，两个构建 job 各给一条警告，安装包里没有 Google Drive 备份。
3. **打标签**：`v<版本>`，必须与 `pyproject.toml` 的版本一致。

   ```bash
   git tag v1.0.0 && git push origin v1.0.0
   ```

   标签触发同一个工作流：先批准两个构建 job，它们成功后再批准 release job。release job 核对安装包，打出源代码包，
   写 `SHA256SUMS`，把这四个文件放进一个**草稿** Release。同一个标签已有 Release 时这一步失败，要先删掉旧的草稿
   再重新运行。
4. **草稿**：从草稿下载两个安装包，在测试机上再装一遍；在"更新内容"下写这个版本的变化，删掉模板里的注释行。
5. **发布**，二选一：
   - **正式发布**，保留"Set as the latest release"：已安装的 Solaris 检查更新时会提示这个版本；
   - **预发布**，勾"Set as a pre-release"：检查更新不会提示它（界面只看 `releases/latest`，它跳过草稿和预发布），
     只能从 Releases 页面手动下载。

检查更新提示的版本：`releases/latest` 的标签去掉 `v` 后是三段数字，并且比当前版本新；Release 里有本机平台的安装包
和 `SHA256SUMS`。下载后核对 SHA-256；macOS 还核对签名、公证和 Team ID；Windows 安装包不签名（D29），只核对
SHA-256。界面从不静默安装，用户确认后才打开安装包。

第一次运行时另外核对：公证通过；磁盘映像的窗口在深色模式下也看得清（只在开发机的浅色模式下看过）；已安装的版本
检查更新显示"已是最新版本"；下一个版本发布后，在旧版本上走一遍检查、下载、校验、打开安装包。

## 5. 工作规则

### 5.1 本机开发不改动本机网络

开发机就是日常使用的机器：不创建 TUN，不设置系统代理，不修改路由和 DNS，不激活系统扩展、不运行组装好的
`Solaris.app`，也不在本机真实注册登录项或计划任务。界面用模拟宿主（`scripts/preview.py`）或无 TUN 的开发服务
（`scripts/dev.py`）调试，经混合端口验证流量；需要 TUN 的测试放在 CI 的网络命名空间或测试机里做
（[architecture.md](architecture.md) §11）。

### 5.2 改动的做法

- **协议**：先改 `proto/solaris/protocol/control.proto`，运行 `scripts/gen_protocol.py`；在同一个提交里同步修改 Go
  分发器、Python `ServiceHost` 和模拟宿主，并用共享样本（`testdata/`）守住两端一致。UI 与服务、扩展总是一起发布，
  协议版本不一致时直接拒绝，不做兼容（[architecture.md](architecture.md) §4.1）。
- **内核的拼装、读订阅、预览和转换**：改了以后运行 `go -C src/core test ./internal/compose -run TestVectors -update`
  更新 `testdata/core-vectors.json`，再按 `tests/test_simulated.py` 的失败改模拟宿主（`tests/support/simulated_core.py`），
  界面的测试才不会依赖内核不再给的回答。
- **视觉**：先改 [DESIGN.md](../DESIGN.md)，再运行 `scripts/gen_theme.py` 生成 `Theme.qml`。
- **文案**：中文是源语言；改动文案后运行 `scripts/gen_i18n.py`，并在 `src/solaris/translations/en.ts` 里补齐英文。
  文案保持中立、简短（DESIGN.md 的 Typography 一节）。
- **决策**：新的设计决策接着编号（D33 起）记进 [decisions.md](decisions.md)；已有的编号不变，代码注释引用它们。
- 每个改动先开 PR，CI 全绿再合并；main 变红时先修。

### 5.3 表结构与存下的格式

1.0.0 起，用户机器上已经存着数据，改动必须让旧数据能继续用：

- **数据库表结构**：改表就在 `src/solaris/models/database.py` 的 `MIGRATIONS` 末尾追加一个迁移，`SCHEMA_VERSION`
  随之加一；已经发布的迁移不再修改。每个迁移都要有测试：用上一个版本的表结构和数据建库，打开后数据完整
  （[storage.md](storage.md) 第 2 节）。
- **存下的格式要让后来的版本能读**：数据库里的设置和记录、备份快照（`solaris-backup`，版本 1）、方案 JSON（版本 1，
  [routing.md](routing.md) §8.5）、Windows 服务 home 里的 `profile.json`、`state.json` 和 `owner.json`。只加有默认值的
  字段；不得不改格式时升版本号，并保留读旧版本的代码。旧版本的应用遇到新格式时：数据库改名放到一边
  （`.bad`）、换一个空库，快照拒绝恢复（`newer_backup`）。

### 5.4 依赖升级

- **mihomo**：钉在 `Meta` 分支的 tag 上（`src/core/go.mod`）。mihomo 的运行时（`hub`、`executor`、`tunnel` 等
  包级状态）只在 engine 里用（`imports_test.go` 检查）；`admission`、`compose`、`vpngate` 用 mihomo 无状态的解析
  函数、类型和常量，升级时这几处也要核对。升级前跑完整测试，并按 [architecture.md](architecture.md) §3.3 逐条复核
  安全要求；3.4 节的体积表随之更新。
- **GEO 数据**的版本和 SHA-256 钉在 `scripts/fetch_geodata.py` 里；改了之后用 `--categories` 重新生成附带的分类名
  清单（`src/solaris/host/geo_categories.py`），CI 用 `--check` 核对。
- **PySide6** 决定 macOS 的最低版本：6.12 要求 macOS 26（D28），升级前先看新版本要求的系统版本。**Nuitka** 的版本是 `scripts/build_app.py` 的 `NUITKA_VERSION`，CI 和
  发布工作流里的 `--with nuitka==` 要一起改。
