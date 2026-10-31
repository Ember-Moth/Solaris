# macOS 原生部分

Xcode 工程由 XcodeGen 从 `project.yml` 生成，包含两个 target（docs/architecture.md §6）：

| target | 产物 | 职责 |
| --- | --- | --- |
| SolarisManager | `SolarisManager.dylib`，C ABI | 激活、替换与停用系统扩展、配置及移除 `NETunnelProviderManager`、启停隧道、查询状态、用 `sendProviderMessage` 收发控制消息；iCloud 备份（CloudKit） |
| SolarisTunnel | `dev.tschen.solaris.tunnel.systemextension` | `NEPacketTunnelProvider`：设置隧道网络和 DNS、取得 utun fd、运行内核；`handleAppMessage` 把消息交给 Go 分发器 |

只构建 arm64（D2），扩展和管理库的最低系统版本是 macOS 15.0（主应用实际要求的版本见“组装 Solaris.app”）。决策
编号见 docs/decisions.md，测试 Mac 上的验收见 docs/maintenance.md 2.2、2.3 节。

扩展用途说明位于 `Tunnel/en.lproj/InfoPlist.strings` 与 `Tunnel/zh-Hans.lproj/InfoPlist.strings`，
由 XcodeGen 的 Tunnel 资源扫描进入最终扩展。基础 `Info.plist` 保留英文回退；主 app 声明 `en`、`zh-Hans`。
`build_apple.check_localizations` 验证编译资源与源码一致，`test_apple_localization.py` 通过独立进程中的
Foundation Bundle 验证中文、英文及其他语言回退，不提交系统扩展请求。实际系统弹窗待测试 Mac 验收（D36）。

provider 初始化时即为 Go 注册本地崩溃日志，保存到扩展自己的 `Solaris/core/crashes`；未配置的 TUN 两分钟后
终止，避免 UI 崩溃后遗留空隧道。系统身份的容器及原生系统报告由管理员提取，不假定它与用户 App Group 相同，
也不为读日志启动隧道。管理库、扩展与 Nuitka UI 的 dSYM 按 UUID 校验并独立保留，见 D37 与维护文档。

## 工程结构

```text
apple/
├── project.yml                      XcodeGen 定义；生成的 Solaris.xcodeproj 不提交
├── App/Solaris.entitlements         主应用（Nuitka 产出的 .app）的 entitlement，由 build_app.py 签上
├── Manager/                         SolarisManager.dylib
│   ├── SolarisManager.h             C 接口，见下文
│   ├── module.modulemap             让 Swift 导入头文件，逐个检查导出函数的签名
│   ├── Exports.swift                @_cdecl 导出
│   ├── SystemExtensionRequests.swift OSSystemExtensionRequest 的激活、替换与停用
│   ├── Deadline.swift               共享的单调时钟期限，覆盖排队与回调
│   ├── TunnelController.swift       VPN 配置、启停、状态、provider message
│   ├── CloudBackup.swift            iCloud 备份：CloudKit 私有数据库里的快照
│   └── ManagerError.swift
└── Tunnel/                          dev.tschen.solaris.tunnel.systemextension
    ├── Info.plist                   NEProviderClasses、NEMachServiceName
    ├── SolarisTunnel.entitlements
    ├── main.swift                   NEProvider.startSystemExtensionMode()
    ├── PacketTunnelProvider.swift   startTunnel、stopTunnel、handleAppMessage
    ├── TunnelNetwork.swift          Go 给出的隧道网络 → NEPacketTunnelNetworkSettings
    ├── TunnelDescriptor.swift       在打开的描述符里找 utun
    └── Core.swift                   调 Go 归档导出的函数
```

生成的 `Solaris.xcodeproj` 不提交（已在 .gitignore）。`project.yml` 是唯一的来源：pbxproj 是生成物，
提交了就要防着两者不一致，还要在 CI 里重新生成再比对，结果仍离不开 XcodeGen；pbxproj 的改动也难以审阅、
容易冲突。生成只要一秒，做法与 hypervpn 相同。

## 构建

需要 Xcode、XcodeGen（`brew install xcodegen`）和 Go，在仓库根目录运行（只构建原生部分；整个 app 见
“组装 Solaris.app”）：

```bash
uv run python scripts/build_apple.py
```

```bash
uv run python scripts/build_apple.py --sign
```

不带参数是不签名构建（`CODE_SIGNING_ALLOWED=NO`），给 CI 和没有证书的机器用；`--sign` 用本机钥匙串里的
Developer ID 证书签名并校验。脚本依次：

1. 用 `scripts/fetch_geodata.py` 取得钉住的 `GeoSite.dat`、`GeoIP.dat`（缓存在 `build/geodata/`），
   `project.yml` 把它们放进扩展的 `Contents/Resources`（见“GEO 数据”）；
2. 把 `src/core/apple` 编成 arm64 的 C archive（`GOPROXY=https://goproxy.cn,direct`），`-ldflags -X` 把版本号写进
   `product.Version`（`GetInfo` 的服务版本），连同 cgo 生成的 `libsolaris.h` 和一份 module map 包成
   `SolarisCore.xcframework`。module map 把模块命名为 `SolarisCore`，并自动链接 Go 的 `net` 包要用的 `libresolv`；
3. 用 XcodeGen 生成工程，`xcodebuild` 构建 Release。扩展的可执行文件在签名前由 Xcode 做 `strip -x`
   （`STRIP_STYLE: non-global`），73.3 MiB 减到 61.3 MiB；
4. 检查产物：只含 arm64，dylib 导出的函数与头文件一致，扩展的 Info.plist 正确，扩展带的 GEO 数据就是钉住的那份；
5. 组装一个检查用的 `Solaris.app`（见“签名”），`--sign` 时签名并校验。

产物都在 `build/apple/`（已被 git 忽略）：

| 路径 | 内容 |
| --- | --- |
| `SolarisManager.dylib` | 管理库 |
| `dev.tschen.solaris.tunnel.systemextension` | 系统扩展 |
| `check/Solaris.app` | 检查用的 app，带上面两个产物 |
| `SolarisCore.xcframework`、`go/` | Go 归档 |
| `xcode/` | DerivedData；每次构建前清掉其中的产物，重新链接和签名 |

- 版本号取自 `pyproject.toml`；`CFBundleVersion` 默认是构建时的 UTC 时间（`--build-number` 可改）。
  系统只会用不同版本的扩展替换已激活的扩展，这样测试 Mac 上每次构建都能替换上去。
- Go 构建标签默认 `with_gvisor`（D3 在 macOS 上的决定，docs/architecture.md §3.4），`--tags` 可以改。
- 想在 Xcode 里打开工程，先跑一次脚本（生成 xcframework 和工程），再打开 `apple/Solaris.xcodeproj`；
  改了 `project.yml` 后重跑脚本，或者 `xcodegen generate --spec apple/project.yml --project apple`。

## 签名

`project.yml` 用手动签名：Team `GY9ZKZMAZA`，证书 `Developer ID Application`，启用 hardened runtime，
签名带安全时间戳（公证需要）。`CODE_SIGN_INJECT_BASE_ENTITLEMENTS` 关掉：否则 Xcode 在 archive 以外的构建里
会给扩展加上 `get-task-allow`，描述文件不允许，公证也会拒绝。

| 产物 | 签名方 | 描述文件 | entitlement |
| --- | --- | --- | --- |
| `SolarisManager.dylib` | Xcode | 无（库不带描述文件） | 无 |
| 系统扩展 | Xcode | `Solaris Tunnel Developer ID`，按名称指定 | `Tunnel/SolarisTunnel.entitlements` |
| 主应用 | 脚本（`codesign`） | `Solaris Developer ID` | `App/Solaris.entitlements` |

主应用是 Nuitka 产出的 .app，由 `scripts/build_app.py` 签名（见“组装 Solaris.app”）。`build_apple.py` 另用
检查用的 `check/Solaris.app` 验证同一条路径：把 dylib 放进 `Contents/Frameworks`、扩展放进
`Contents/Library/SystemExtensions`，可执行文件是一个链接了头文件里全部函数的桩程序（从不运行）。

`--sign` 时脚本：

- 按名称找两份描述文件（取未过期、Team 匹配、最新的一份），再从钥匙串里找两份描述文件都授权的
  Developer ID Application 证书，以 SHA-1 交给 `xcodebuild` 和 `codesign`；
- 嵌入主应用的描述文件，用 `App/Solaris.entitlements` 签检查用的 app；
- 校验：app 通过 `codesign --verify --deep --strict`，扩展再单独 `--verify --strict` 一次（app 把
  `Contents/Library/SystemExtensions` 当资源而不是嵌套代码封存，`--deep` 只核对扩展的文件，不验证扩展自己的
  签名）；dylib、扩展、app 都由这张证书签名，Team 正确，带
  hardened runtime 和时间戳；扩展和 app 实际签上的 entitlement 与仓库里的文件完全一致，并且都在各自描述
  文件允许的范围内；嵌入的描述文件就是按名称找到的那一份。

脚本不公证，不安装也不激活系统扩展。两份 entitlement 文件都写明了 `com.apple.application-identifier`
和 `com.apple.developer.team-identifier`，这样签上的内容与文件逐字一致。

## 组装 Solaris.app

```bash
uv run --with nuitka==4.2.2 python scripts/build_app.py
```

```bash
uv run --with nuitka==4.2.2 python scripts/build_app.py --unsigned
```

脚本先按 `build_apple.py` 的步骤构建原生部分（带签名，或 `--unsigned` 时不签名），再：

1. 用 Nuitka 4.2.2 把 `package/entry.py` 编成 app（`--mode=app`），带上 `src/solaris/ui`（QML、托盘图标、
   字体）和编译好的翻译。`--include-qt-plugins=qml` 会带进全部 QML 模块，脚本按清单排除界面用不到的
   （3D、图表、多媒体、WebEngine、其他控件样式等），app 从 428 MiB 减到 201 MiB；Qt 的 TLS 只留 Secure Transport。
   编译用 Xcode 的 clang，不用 ccache（ccache 要求 PATH 里同名的 clang 就是所用的那个）；
2. 检查 UI 的每个 Mach-O：只含 arm64，只加载 bundle 内和系统的库；install name 和 rpath 里残留的构建机
   路径（uv 的 Python 的 `libpython`）改成 `@rpath` 或删掉；
3. 组装 `build/app/Solaris.app`：Nuitka 产物在 `Contents/MacOS`，管理库在 `Contents/Frameworks`，扩展在
   `Contents/Library/SystemExtensions`。Info.plist：bundle id `dev.tschen.solaris`，版本与扩展相同，注册
   URL scheme `solaris` 和 `clash`（D6，链接由界面处理），`SolarisCoreVersion` 是 mihomo 的版本（隧道未连接时
   `MacExtensionHost` 用它回答 `GetInfo`）；
4. 签名：UI 的 Mach-O 逐个用 Developer ID 签（hardened runtime、时间戳），再嵌入 `Solaris Developer ID`
   描述文件，用 `App/Solaris.entitlements` 签整个 app；dylib 和扩展保留 Xcode 的签名。`--unsigned` 时整个 app
   做 ad hoc 签名，只供查看，不能激活扩展；
5. 校验：除 `build_apple.py` 的各项外，`Contents/MacOS` 里每个 Mach-O 都由这个 Team 签名、带 hardened runtime
   和时间戳。

- **可执行文件叫 `SolarisUI`**：卷不区分大小写，而 `Contents/MacOS/solaris/` 是 Python 包的数据目录（`ui/`、
  `translations/`），编译后的模块在那里找它们。程序坞、菜单栏和“登录项”显示的是 `CFBundleName`（Solaris）。
- `Contents/MacOS` 里的数据文件（QML、字体、翻译）带 Nuitka 的 ad hoc 签名，签名存在扩展属性里，复制时要用
  `ditto` 保留；hypervpn 同样的布局能通过公证。
- **最低系统版本**：PySide6 6.12.0（`uv.lock` 锁定的版本）的 wheel 标着 macOS 14.0，绑定库实际的部署版本却是
  26.0，所以脚本把 app 的 `LSMinimumSystemVersion` 设为 bundle 里所有 Mach-O 要求的最高版本（现在是 26.0），
  并打印警告。最低支持 macOS 26（决策 D28），所以不为 macOS 15 把 PySide6 降到 6.11.x。扩展和管理库的部署目标
  仍是 15.0。
- **JIT**：主应用的 entitlement 带 `com.apple.security.cs.allow-jit`，Qt Quick 的 JavaScript 引擎在 hardened
  runtime 下要用（hypervpn 同样需要）；它不需要描述文件授权。
- 脚本不运行、不安装 app，也不公证。

## 磁盘映像

```bash
uv run --no-project --with dmgbuild==1.6.7 python scripts/build_dmg.py \
  build/app/Solaris.app build/release/Solaris-<版本>-macos-arm64.dmg
```

`scripts/build_dmg.py` 用 dmgbuild 1.6.7 把构建好的 `Solaris.app`（签名的或 `--unsigned` 的都行）做成磁盘映像：

- 卷名 Solaris，卷图标是 `package/icons/solaris.icns`（`scripts/gen_icons.py`）；
- 挂载后打开一个固定的 Finder 窗口，600 × 360 点，不显示工具栏、侧边栏和状态栏，128 点的大图标：左边 Solaris.app，
  右边“应用程序”的快捷方式，中间一条细箭头，没有文字。效果见 `package/dmg/preview.png`；
- 背景是 `package/dmg/background.png` 和 `background@2x.png`，由 `scripts/gen_dmg_background.py` 按
  `build_dmg.py` 里的布局画出后提交，dmgbuild 把两张合成一个多分辨率 TIFF。Finder 的背景不随深色模式切换，
  图标名字却是浅色模式黑字、深色模式白字，所以底色取中性灰 `#72767E`，与黑、白的对比度都约 4.6:1；
- HFS+，LZMA 压缩（ULMO，macOS 10.15 起支持），缩到内容的大小；
- 不用 dmgbuild 的 `hide_extensions`：它用 SetFile 在 app 上写 Finder 信息，app 的签名就通不过
  `codesign --verify --strict`。

dmgbuild 自己写 `.DS_Store`，不经过 Finder，所以在 CI 的 runner 上也能做。做完后脚本检查：`hdiutil verify` 通过；
只读挂载（不在 Finder 里显示）后，app 通过 `codesign --verify --deep --strict`，快捷方式指向 `/Applications`，
背景、卷图标和 `.DS_Store` 都在，卷带自定义图标标记。脚本不签名、不公证。CI 的 `apple` job 用 ad hoc 签名的检查用
app 做一次映像。改了布局后运行 `uv run python scripts/gen_dmg_background.py`，挂载映像看一眼。

## 发布工作流

`.github/workflows/release.yml` 由 `v*` 标签和手动触发（`workflow_dispatch`），job 使用 `release`
Environment，Ember-Moth 批准后才拿到机密。标签必须是 `v<pyproject.toml 的版本>`。步骤：

1. 建临时钥匙串，导入证书和签发它的 Developer ID G2 中间证书（`apple/certificates/DeveloperIDG2CA.cer`，不依赖
   runner 镜像带不带它），核对 `MACOS_CERTIFICATE_NAME` 属于 `APPLE_TEAM_ID`，且是有效的身份；找不到时列出钥匙串里
   每个身份和它无效的原因；两份描述文件
   装进 `~/Library/Developer/Xcode/UserData/Provisioning Profiles`，Xcode 和 `build_apple.py` 按名称找；
2. `scripts/build_app.py` 构建、签名、校验；
3. 用 `notarytool`（App Store Connect API Key）公证 app 并 staple，`spctl` 评估；
4. `scripts/build_dmg.py` 做 `Solaris-<版本>-macos-arm64.dmg`（见“磁盘映像”），签名、公证、staple，生成
   `Solaris-<版本>-macos-arm64.dmg.sha256`；
5. 两个文件作为 workflow artifact `solaris-<版本>-macos-arm64` 上传，保留 30 天；
6. 无论成败都删除钥匙串、描述文件和 API Key。

公证由 `scripts/notarize.py` 完成，它只在 GitHub Actions 里运行。对 `v*` 标签，工作流最后的 release job 把两个平台的
安装包放进一个草稿 GitHub Release（docs/release.md §1.1）；发布的步骤见 docs/maintenance.md 第 4 节。

## GEO 数据

安装包附带的 `GeoSite.dat`、`GeoIP.dat`（docs/routing.md §8.4）在扩展的 `Contents/Resources` 里。扩展的
mihomo home 在扩展容器的 `Application Support/Solaris/core`，`startTunnel` 把资源目录交给
`solaris_attach_tun`，Go 在打开 engine 前把 home 里缺少的文件复制过去（先写临时文件再改名，不会留下半个
文件）；已有的文件不动，engine 的 GEO 自动更新会替换它们。复制失败时 attach 失败，隧道不启动。

## C 接口（`Manager/SolarisManager.h`）

| 函数 | 作用 |
| --- | --- |
| `solaris_manager_activate(timeout, &error)` | 激活包内的系统扩展，替换其他构建激活的扩展 |
| `solaris_manager_configure(include_all_networks, &error)` | 创建或修复 VPN 配置并启用，按 `include_all_networks` 写 `includeAllNetworks` 和 `excludeLocalNetworks`（D19）；第一次保存时系统请用户允许 |
| `solaris_manager_start(timeout, include_all_networks, &error)` | 必要时先配置（同上），再启动隧道，等到已连接 |
| `solaris_manager_stop(timeout, &error)` | 停止隧道，等到断开；本来就没运行时直接成功 |
| `solaris_manager_status(&status, &error)` | 隧道状态：`SOLARIS_TUNNEL_*`，对应 `NEVPNStatus`，另加“未配置” |
| `solaris_manager_send(request, length, timeout, &response, &response_length, &error)` | 发一条编码好的 Request，取回 Response |
| `solaris_manager_icloud_status(timeout, &status, &error)` | 系统 iCloud 账户的状态：`SOLARIS_ICLOUD_*`，对应 `CKAccountStatus`，另加"这个构建没有 iCloud entitlement"（`UNSUPPORTED`） |
| `solaris_manager_icloud_save(name, info, snapshot, length, timeout, &error)` | 把一份快照存成名为 `name` 的记录，同名的覆盖 |
| `solaris_manager_icloud_list(timeout, &list, &error)` | 列出全部快照，不含内容：`[{"name", "info"}]` 的 JSON |
| `solaris_manager_icloud_download(name, timeout, &snapshot, &length, &error)` | 下载一份快照；没有时返回 `NOT_FOUND` |
| `solaris_manager_icloud_delete(name, timeout, &error)` | 删除一份快照；本来就没有时直接成功 |
| `solaris_manager_free(pointer)` | 释放本库交出的字符串或缓冲区 |

约定：

- **返回值**：除 `solaris_manager_free` 外都返回 `SolarisManagerResult`（`int32_t`）：`OK`、`FAILED`、
  `TIMED_OUT`、`NOT_CONFIGURED`（还没有 VPN 配置）、`NOT_CONNECTED`（隧道未连接，扩展没在运行）、
  `NEEDS_APPROVAL`（等用户在系统设置里允许扩展）、`NEEDS_REBOOT`（重启后完成激活），以及 iCloud 的
  `NOT_SIGNED_IN`、`NOT_FOUND`、`QUOTA_EXCEEDED`、`UNREACHABLE`（网络或服务暂时不可用）。
- **错误信息**：结果不是 `OK` 且 `error` 不为 NULL 时，`*error` 是一段 UTF-8 说明；否则置为 NULL。
- **内存**：传入的缓冲区归调用方所有，只在调用期间读取；经指针参数交出的字符串和缓冲区归调用方，
  用 `solaris_manager_free` 释放（空的 Response 也是一块要释放的缓冲区）。
- **线程**：调用会阻塞到完成或超时。NetworkExtension 在主队列上完成回调，所以要在后台线程调用，并且
  主线程在跑 run loop（Qt 的事件循环就是）；在主线程调用会直接返回 `FAILED`。`configure`、`start`、
  `stop` 依次执行，`status` 和 `send` 可以与它们并行，`icloud_*` 与所有调用都可以并行。
- **期限**：隧道管理用 `Deadline` 的单调时钟期限；等待启停队列、加载/保存/重载 VPN 配置和等待隧道状态都扣同一份预算。
  过期的回调不再触发下一步。Go 控制请求另有逐层递减预算（architecture.md 4.1.1 节）；attach 最多 50 秒、detach 最多
  20 秒，卡住时结束扩展宿主，让系统回收资源，不在同一进程里重建第二个内核。
- **激活**：系统要用户批准时，`activate` 立即返回 `NEEDS_APPROVAL`，请求继续挂着；之后的调用复用同一个待批准请求，
  仍未允许就立即返回 `NEEDS_APPROVAL`，不再等一分钟。用户允许并完成激活后才能返回 `OK`。替换时总是用包内的扩展，因为只有它和
  这个 app 的控制协议一致。
- **停用**：`solaris_manager_deactivate` 在同一份期限内停止所有指向 Solaris provider 的 VPN、请求停用扩展，只有系统返回
  completed（或确认扩展已不存在）后，才重新读取并删除本应用的 VPN 配置。其他 VPN、订阅、设置与凭据不在删除范围内。
  需要用户处理或系统尚未完成时保留配置，Python 显示待处理状态并允许重试；`BUSY` 表示相反方向的请求仍未完成，
  `CANCELLED` 表示用户取消。超时请求保持到原回调结束，同方向重试复用它，不重复提交。系统结束请求后允许重新查询，
  不把未完成结果缓存成永久拒绝。测试用注入的提交器调用 delegate，不向本机提交真实系统请求。
- **配置**（D7）：`start` 不带内核的配置；连接后，宿主用 `send` 发 `Start` / `Reload`。
- **包含所有网络**（D19）：`include_all_networks` 是 `bool`。VPN 配置的 `includeAllNetworks` 与它不同时，
  `configure` 和 `start` 重写并保存配置，`excludeLocalNetworks` 随它一起开关，让局域网流量照旧不进隧道。只在隧道
  断开时改写：扩展只在 attach 时读一次，所以隧道没断开时改的设置下次连接时生效；`start` 遇到正在断开的隧道，
  等它断开后再写。
- Swift 侧导入同一份头文件，编译时逐个比对导出函数的签名；构建脚本再核对 dylib 实际导出的符号。

预期的调用顺序：`activate`（`NEEDS_APPROVAL` 时提示用户并继续调用）→ `start` → `send(Start)` →
`send(...)` → `stop`。

## Go 归档

`src/core/apple` 导出的函数（cgo 生成 `libsolaris.h`），扩展在 `Tunnel/Core.swift` 里调用：

| 函数 | 作用 |
| --- | --- |
| `solaris_tunnel_network(&length)` | 隧道的网络（`admission.FDNetwork`）的 JSON：IPv4 地址和掩码、IPv6 地址和前缀长度、DNS 服务器、MTU |
| `solaris_attach_tun(fd, home, resources, include_all_networks)` | 把 `resources` 里的 GEO 数据复制进 `home`（缺少时），再以 fd 模式打开 engine 和分发器；`include_all_networks` 是扩展从 `protocolConfiguration` 读到的 `includeAllNetworks`，打开时 engine 把 `system`、`mixed` 栈换成 gVisor（D19）；返回 NULL 或错误字符串 |
| `solaris_detach_tun()` | 取消在途消息并等它们结束，停止内核，关闭分发器和 Go 的描述符副本 |
| `solaris_handle_message(request, length, &response_length)` | 处理一条编码好的 Request，返回 Response；没有 attach 时返回 `not_running` |
| `solaris_free(pointer)` | 释放上面几个函数返回的缓冲区和字符串 |

- **地址只定义一处**：隧道的地址、DNS 服务器和 MTU 都在 Go 的 `admission.FDNetwork` 里，扩展从
  `solaris_tunnel_network` 读来设置 utun，准入流水线的 fd 补丁也用它；Go 的测试核对补丁解析出的 TUN 与它一致。
- **描述符归 NE**：`startTunnel` 在网络设置完成后找到 utun 的描述符（与 hypervpn 的
  `AppleRawTunFileDescriptor.locate` 相同的做法），交给 `solaris_attach_tun`。Go 从不关闭它，mihomo 只用它的
  副本。`stopTunnel` 先 `solaris_detach_tun`（Go 关掉自己的副本），再调用 completion handler，之后 NE 关闭
  描述符。attach 和 detach 在扩展的一个串行队列上按序执行。
- 消息在并发队列上分发，状态变化由分发器内部的操作锁串行。Swift 只转发字节，不解析消息。

## Python 宿主

`src/solaris/host/extension.py` 的 `MacExtensionHost` 是 macOS 正式版的宿主：`ManagerLibrary` 经 ctypes 绑定
`SolarisManager.dylib`，每个 Request 用一次 `solaris_manager_send` 发给扩展，请求和响应的映射沿用
`ServiceHost`。阻塞调用各在一个 daemon 线程里执行：NetworkExtension 在主队列上完成回调，app 退出时没人处理
主队列，进行中的调用要等到超时，daemon 线程不会拖住退出。

- **启动**：本次运行第一次连接时 `activate`（要用户批准时报 `extension_needs_approval`，需要重启时报
  `extension_needs_reboot`），再 `start`（`include_all_networks` 取自 `RunOptions.include_all_networks`，即设置页的
  "包含所有网络"），然后 `send(Start)`；内核拒绝配置时把隧道停掉，不让流量进入没有内核的
  隧道。有断开时存下的未读订阅时，`prepare_start` 先做 `activate` 和 `start`，界面读完这些订阅（`InspectSource`、
  `Validate`）再发 `Start`（D27，ui.md 第 3 节）；其间流量停在没有配置的隧道里。**停止**：`stop`，扩展 detach 时停止内核。关闭宿主不动隧道。
- **隧道未连接**（`NOT_CONNECTED`、`NOT_CONFIGURED`，或扩展回答没有 attach）：`get_status` 按隧道状态报
  stopped / starting / stopping，`get_info` 用 Info.plist 里的内核版本，列表和轮询返回空，GEO 分类用安装包的清单，
  其余需要内核的方法报 `not_running`；订阅随扩展一起消失，之后重新订阅。`core_available` 在扩展回答后为真，
  得到 `NOT_CONNECTED` 等结果后为假，界面据此隐藏要内核的部分。
- 其他结果：`TIMED_OUT` → `timeout`；`send` 的 `FAILED` → `connection_closed`，启停的 `FAILED` → `tunnel_failed`。
- 选宿主：打包的 app 里（`Contents/Frameworks` 有管理库）用 `MacExtensionHost`，除非设了
  `SOLARIS_DEV_SOCKET`；从源码运行时照旧用开发服务。
- 单元测试用 Python 写的假管理库，以及用真实头文件编出的假 dylib（检查 ctypes 签名和每块缓冲区都被释放），
  不碰 NetworkExtension。

## iCloud 备份

设计见 docs/storage.md 第 5 节。管理库被加载进主应用的进程，所以 CloudKit 用的是主应用的签名和
`App/Solaris.entitlements`：container `iCloud.dev.tschen.solaris`、服务 `CloudKit`、环境 `Production`（Developer ID
只能用 Production）。扩展不访问 iCloud，它的 entitlement 不变。

- 每份快照是私有数据库默认 zone 里一条 `Snapshot` 类型的记录：字符串字段 `info`（Python 写的 JSON：时间、设备、
  版本、配置和方案的份数、大小），资源字段 `snapshot`（快照本身）。列出用 `CKQuery`（`TRUEPREDICATE`），只取 `info`。
- 没有这个 container 的 entitlement 时（不签名或 ad hoc 签名的构建），CloudKit 会抛出结束进程的 Objective-C 异常，
  所以 `CloudBackup` 先用 `SecTaskCopyValueForEntitlement` 读自己进程的 entitlement，没有就不创建 `CKContainer`：
  状态是 `UNSUPPORTED`，其余调用返回"not signed for iCloud"，进程不受影响。
- 操作用 `userInitiated` 的服务质量（更低的会被系统推迟），超时时取消操作。

### Schema

Production 环境不能由应用自动建 schema：`Snapshot` record type（`info` 为 String，`snapshot` 为 Asset，`recordName`
带 Queryable 索引，列出快照的查询要用）要在 CloudKit Console 的 Development 环境里建好，再部署到 Production。
Production 的 schema 之后只能加不能删，所以字段只加不改。步骤见 docs/maintenance.md 2.3 节。没部署时，列出快照报
"The iCloud container has no Snapshot record type; deploy its schema"，保存报 CloudKit 的"Cannot create new type"，
都会写在设置页 iCloud 一行。

### 签名材料

App ID `dev.tschen.solaris` 已开 iCloud（CloudKit），container 已建（见“Apple Developer 配置”）。`Solaris Developer ID`
描述文件要授权 `com.apple.developer.icloud-container-identifiers`（含 `iCloud.dev.tschen.solaris`）、
`icloud-services`（`CloudKit` 或 `*`）和 `icloud-container-environment`（含 `Production`）；`build_app.py` 的签名构建会
逐项核对 entitlement 与描述文件，现在的签名构建能通过就说明已授权。若报"does not allow
com.apple.developer.icloud-container-identifiers"，在开发者网站编辑 App ID 的 iCloud → Configure，勾上这个 container，
重新生成 `Solaris Developer ID`，更新 `MACOS_APP_PROFILE_BASE64`。

## Apple Developer 配置

分发方式是 Developer ID（不上 App Store），系统扩展是 PacketTunnel。参照 hypervpn-desktop 已跑通的做法。

**Team ID**：`GY9ZKZMAZA`

### 标识

| 类型 | 标识 | 能力 |
| --- | --- | --- |
| App ID（主应用） | `dev.tschen.solaris` | System Extension、Network Extensions、App Groups、iCloud（CloudKit） |
| App ID（系统扩展） | `dev.tschen.solaris.tunnel` | Network Extensions、App Groups |
| App Group | `group.dev.tschen.solaris` | 留给大配置的共享容器（D8） |
| iCloud Container | `iCloud.dev.tschen.solaris` | iCloud 备份（storage.md 第 5 节） |

### 证书与描述文件

- **证书**：Developer ID Application，G2 签发，2027-11-29 到期。证书每年要续：续期后重新生成两份描述文件，再更新
  `MACOS_CERTIFICATE_*` 和两个描述文件机密。新建证书要在开发者网站上选 G2 Sub-CA；G1 签发的证书只能用到
  2027-02-01。
- **描述文件**：`Solaris Developer ID`、`Solaris Tunnel Developer ID`，都授权上面这张证书，都含 App Group
  `group.dev.tschen.solaris`。
- **公证**：Herta Network LLC 的 Team Key `2QSRW72GKA`（Developer 权限）。

### 测试机与调试签名

系统扩展会创建 utun，只在单独的 Apple Silicon 测试 Mac 或 macOS 虚拟机上运行，不在开发机上运行。调试时可以登记
测试 Mac 的 Provisioning UDID，用 Apple Development 证书建两份 macOS App Development 描述文件，就不必每次公证。

### entitlement

| 目标 | entitlement |
| --- | --- |
| 主应用 | `com.apple.developer.system-extension.install`；`com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；`com.apple.security.application-groups` 含 `group.dev.tschen.solaris`；iCloud 的 CloudKit 容器 `iCloud.dev.tschen.solaris`（环境 Production，管理库的 iCloud 备份在主应用进程里用它）；`com.apple.security.cs.allow-jit`（Qt Quick） |
| 系统扩展 | `com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；App Sandbox；`network.client` / `network.server`；同一个 App Group |

两份描述文件分别嵌入为主应用和扩展的 `embedded.provisionprofile`，签名时启用 hardened runtime。

### 机密的保管

证书的 `.p12` 及其密码、两份描述文件、`.p8` 私钥都不进仓库，也不贴到对话里。发布 CI 从 GitHub 的 `release`
Environment 读取它们；这个 Environment 只允许 `main` 分支和 `v*` 标签部署，每次部署都要 Ember-Moth 批准。

| 名称 | 类型 | 内容 |
| --- | --- | --- |
| `APPLE_TEAM_ID` | 变量 | `GY9ZKZMAZA` |
| `MACOS_CERTIFICATE_NAME` | 变量 | `Developer ID Application: Herta Network LLC (GY9ZKZMAZA)` |
| `APP_STORE_CONNECT_KEY_ID` | 变量 | `2QSRW72GKA`（Herta Network LLC 的 Team Key，Developer 权限） |
| `APP_STORE_CONNECT_ISSUER_ID` | 变量 | API Key 的 Issuer ID |
| `APP_STORE_CONNECT_API_KEY` | 机密 | API Key 的 `.p8` 私钥 |
| `KEYCHAIN_PASSWORD` | 机密 | 临时钥匙串的密码（随机生成） |
| `MACOS_CERTIFICATE_BASE64`、`MACOS_CERTIFICATE_PASSWORD` | 机密 | G2 证书的 `.p12` 及其密码，SHA-1 `F462D4E8…FC33`，2027-11-29 到期 |
| `MACOS_APP_PROFILE_BASE64`、`MACOS_NETWORK_EXTENSION_PROFILE_BASE64` | 机密 | `Solaris Developer ID`、`Solaris Tunnel Developer ID`，都授权上面这张证书 |

`.github/workflows/release.yml` 读取它们（见“发布工作流”）。它和 `build_app.py` 一起校验证书、Team、有效期，
以及两份描述文件的权限：主应用必须有 `system-extension.install`，扩展必须有
`packet-tunnel-provider-systemextension`。
