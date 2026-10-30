# macOS 原生部分

Xcode 工程由 XcodeGen 从 `project.yml` 生成，包含两个 target（docs/architecture.md §6）：

| target | 产物 | 职责 |
| --- | --- | --- |
| SolarisManager | `SolarisManager.dylib`，C ABI | 激活和替换系统扩展、配置 `NETunnelProviderManager`、启停隧道、查询状态、用 `sendProviderMessage` 收发控制消息；iCloud 备份（CloudKit） |
| SolarisTunnel | `dev.tschen.solaris.tunnel.systemextension` | `NEPacketTunnelProvider`：设置隧道网络和 DNS、取得 utun fd、运行内核；`handleAppMessage` 把消息交给 Go 分发器 |

只构建 arm64（D2），最低系统版本 macOS 15.0（主应用实际要求的版本见“组装 Solaris.app”）。

## 工程结构

```text
apple/
├── project.yml                      XcodeGen 定义；生成的 Solaris.xcodeproj 不提交
├── App/Solaris.entitlements         主应用（Nuitka 产出的 .app）的 entitlement，由 build_app.py 签上
├── Manager/                         SolarisManager.dylib
│   ├── SolarisManager.h             C 接口，见下文
│   ├── module.modulemap             让 Swift 导入头文件，逐个检查导出函数的签名
│   ├── Exports.swift                @_cdecl 导出
│   ├── ExtensionActivation.swift    OSSystemExtensionRequest 的激活与替换
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
  并打印警告；要支持 macOS 15，得把 PySide6 钉在 6.11.x（hypervpn 用的 6.11.2 是 15.0）。扩展和管理库仍是 15.0。
- **JIT**：主应用的 entitlement 带 `com.apple.security.cs.allow-jit`，Qt Quick 的 JavaScript 引擎在 hardened
  runtime 下要用（hypervpn 同样需要）；它不需要描述文件授权。
- 脚本不运行、不安装 app，也不公证。

## 发布工作流

`.github/workflows/release.yml` 由 `v*` 标签和手动触发（`workflow_dispatch`），job 使用 `release`
Environment，Ember-Moth 批准后才拿到机密。标签必须是 `v<pyproject.toml 的版本>`。步骤：

1. 建临时钥匙串，导入证书（核对 `MACOS_CERTIFICATE_NAME` 属于 `APPLE_TEAM_ID`，且是有效的身份）；两份描述文件
   装进 `~/Library/Developer/Xcode/UserData/Provisioning Profiles`，Xcode 和 `build_apple.py` 按名称找；
2. `scripts/build_app.py` 构建、签名、校验；
3. 用 `notarytool`（App Store Connect API Key）公证 app 并 staple，`spctl` 评估；
4. 做 HFS+、LZMA 压缩的 `Solaris-<版本>-macos-arm64.dmg`（内含 Solaris.app 和“应用程序”的快捷方式），签名、
   公证、staple，生成 `Solaris-<版本>-macos-arm64.dmg.sha256`；
5. 两个文件作为 workflow artifact `solaris-<版本>-macos-arm64` 上传，保留 30 天；
6. 无论成败都删除钥匙串、描述文件和 API Key。

公证由 `scripts/notarize.py` 完成，它只在 GitHub Actions 里运行。还不发布 GitHub Release（M6）。本机试过的
部分：完整的签名构建和校验，以及做 DMG、签名、`hdiutil verify` 和校验和（49 MB）；公证和工作流本身没有运行过。

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
- **激活**：系统要用户批准时，`activate` 立即返回 `NEEDS_APPROVAL`，请求继续挂着；之后的调用等同一个请求，
  用户允许后返回 `OK`，到超时仍未允许则再返回 `NEEDS_APPROVAL`。替换时总是用包内的扩展，因为只有它和
  这个 app 的控制协议一致。
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
| `solaris_tunnel_network(&length)` | 隧道的网络（`engine.FDNetwork`）的 JSON：IPv4 地址和掩码、IPv6 地址和前缀长度、DNS 服务器、MTU |
| `solaris_attach_tun(fd, home, resources, include_all_networks)` | 把 `resources` 里的 GEO 数据复制进 `home`（缺少时），再以 fd 模式打开 engine 和分发器；`include_all_networks` 是扩展从 `protocolConfiguration` 读到的 `includeAllNetworks`，打开时 engine 把 `system`、`mixed` 栈换成 gVisor（D19）；返回 NULL 或错误字符串 |
| `solaris_detach_tun()` | 取消在途消息并等它们结束，停止内核，关闭分发器和 Go 的描述符副本 |
| `solaris_handle_message(request, length, &response_length)` | 处理一条编码好的 Request，返回 Response；没有 attach 时返回 `not_running` |
| `solaris_free(pointer)` | 释放上面几个函数返回的缓冲区和字符串 |

- **地址只定义一处**：隧道的地址、DNS 服务器和 MTU 都在 Go 的 `engine.FDNetwork` 里，扩展从
  `solaris_tunnel_network` 读来设置 utun，engine 的 fd 补丁也用它；Go 的测试核对补丁解析出的 TUN 与它一致。
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
  所以 `CloudBackup` 先用 `SecTaskCopyValueForEntitlement` 读自己进程的 entitlement，没有就不创建 `CKContainer`。
  这一点在开发机上用不签名的 dylib 试过：状态是 `UNSUPPORTED`，其余调用返回"not signed for iCloud"，进程不受影响。
- 操作用 `userInitiated` 的服务质量（更低的会被系统推迟），超时时取消操作。

### 要在 CloudKit Console 里做的事（一次）

Production 环境不能由应用自动建 schema，所以 record type 要先在 Development 里建好再部署：

1. 打开 [CloudKit Console](https://icloud.developer.apple.com)，选 container `iCloud.dev.tschen.solaris`，
   进 Development 环境的 Schema → Record Types，新建 `Snapshot`，加两个字段：`info`（String）、`snapshot`（Asset）。
2. 同一 record type 的 Indexes 里给 `recordName` 加一个 Queryable 索引（列出快照的查询要用；不加会报
   "Field 'recordName' is not marked queryable"）。
3. Deploy Schema Changes，部署到 Production。Production 的 schema 之后只能加不能删。

没部署时，列出快照报"The iCloud container has no Snapshot record type; deploy its schema"，保存报 CloudKit 的
"Cannot create new type"，都会写在设置页 iCloud 一行。

### 签名材料（不需要改，核对即可）

App ID `dev.tschen.solaris` 已开 iCloud（CloudKit），container 已建（见“Apple Developer 配置”）。`Solaris Developer ID`
描述文件要授权 `com.apple.developer.icloud-container-identifiers`（含 `iCloud.dev.tschen.solaris`）、
`icloud-services`（`CloudKit` 或 `*`）和 `icloud-container-environment`（含 `Production`）；`build_app.py` 的签名构建会
逐项核对 entitlement 与描述文件，现在的签名构建能通过就说明已授权。若报"does not allow
com.apple.developer.icloud-container-identifiers"，在开发者网站编辑 App ID 的 iCloud → Configure，勾上这个 container，
重新生成 `Solaris Developer ID`，更新 `MACOS_APP_PROFILE_BASE64`。

### 测试 Mac 上的验证

用签名、公证过的 app，登录一个测试用的 Apple 账户：

1. 设置页「备份与同步」有 iCloud 一行，写「未开启」。打开开关，确认框写明不加密、含订阅链接和配置原文；「开启」后
   写「上次备份：刚刚 · 成功」。CloudKit Console 的 Production → Records（用同一个 Apple 账户登录，Private
   Database，`Snapshot`）里有一条记录。
2. 改一个设置或导入一份订阅，约 35 秒后多一条记录；什么都不改时不再多。「立即备份」每次都多一条；超过 10 条后
   最旧的被删掉。
3. 另一台 Mac（或删掉 `~/Library/Application Support/Solaris` 后）打开开关：不上传空数据；「从云端恢复…」列出快照，
   恢复后订阅、方案、覆写、设置都回来，订阅重新下载。
4. 「更多」→「删除云端备份」后 Console 里记录全部消失，iCloud 一行写「尚未备份」。
5. 断网后「立即备份」：写「上次备份失败：无法连接，稍后会再试」；联网后一小时内（或下次启动）自动补上。
6. 在系统设置里退出 iCloud：一行写「系统未登录 iCloud」，「打开系统设置」打开 Apple 账户那一页；重新登录后一分钟
   内恢复。
7. `build_app.py --unsigned` 的 app：一行写「仅签名的安装版可用」，打开设置页不崩溃。
8. 账户的 iCloud 空间满时，失败原因是「储存空间已满」。

## 尚未完成

- **最低系统版本**：PySide6 6.12.0 让 app 要求 macOS 26（见“组装 Solaris.app”，roadmap 的 D28）。
- **测试 Mac 上的实测**：激活、批准、连接；utun 描述符的查找、四种栈经过 utun 的流量、DNS 劫持、断开与
  重连后描述符的归属；provider message 的大小上限（D8）；app 在 hardened runtime 下的启动；`includeAllNetworks`：
  打开后系统默认绕开隧道的流量确实进入隧道、局域网流量不进，扩展自己（mihomo 的出站）的流量不被卷回隧道，
  `mips` 和 `gvisor` 栈都能工作，保存配置时系统是否再次请用户允许；断开时导入订阅后连接，`Start` 之前读订阅
  花多久。
- **发布工作流**还没有运行过（要在 `release` Environment 里批准）；公证结果未知。
- **iCloud 备份**没在真机上运行过：CloudKit 的 schema 还要部署（见“iCloud 备份”），之后按那里的步骤在测试 Mac 上验证。
- 批准扩展、需要重启等新错误码的界面文案；卸载前停用扩展（`deactivationRequest`）、扩展说明文字的
  本地化。

## Apple Developer 配置

分发方式是 Developer ID（不上 App Store），系统扩展是 PacketTunnel。参照 hypervpn-desktop 已跑通的做法。

**Team ID**：`GY9ZKZMAZA`

### 已建好（2026-10-29）

| 类型 | 标识 | 能力 |
| --- | --- | --- |
| App ID（主应用） | `dev.tschen.solaris` | System Extension、Network Extensions、App Groups、iCloud（CloudKit） |
| App ID（系统扩展） | `dev.tschen.solaris.tunnel` | Network Extensions、App Groups |
| App Group | `group.dev.tschen.solaris` | 留给大配置的共享容器（D8） |
| iCloud Container | `iCloud.dev.tschen.solaris` | iCloud 备份（storage.md 第 5 节） |

### 签名材料（2026-10-30 已齐）

- **证书**：Developer ID Application，G2 签发，2027-11-29 到期。证书每年要续：续期后重新生成两份描述文件，再更新
  `MACOS_CERTIFICATE_*` 和两个描述文件机密。新建证书要在开发者网站上选 G2 Sub-CA；G1 签发的证书只能用到
  2027-02-01。
- **描述文件**：`Solaris Developer ID`、`Solaris Tunnel Developer ID`，都授权上面这张证书，都含 App Group
  `group.dev.tschen.solaris`。
- **公证**：Herta Network LLC 的 Team Key `2QSRW72GKA`（Developer 权限）。

### 还要准备（M5 开始前）

- **测试机**：一台单独的 Apple Silicon Mac 或 macOS 虚拟机。系统扩展会创建 utun，不在开发机上运行。
- **调试签名**（可选）：登记测试 Mac 的 Provisioning UDID，用 Apple Development 证书建两份 macOS App Development
  描述文件，调试时就不必每次公证。
- **最低系统版本**：工程暂定 macOS 15.0（PySide6 绑定库实际的部署版本是 15.0），待确认。

### entitlement

| 目标 | entitlement |
| --- | --- |
| 主应用 | `com.apple.developer.system-extension.install`；`com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；`com.apple.security.application-groups` 含 `group.dev.tschen.solaris`；iCloud 的 CloudKit 容器 `iCloud.dev.tschen.solaris`（环境 Production，管理库的 iCloud 备份在主应用进程里用它）；`com.apple.security.cs.allow-jit`（Qt Quick） |
| 系统扩展 | `com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；App Sandbox；`network.client` / `network.server`；同一个 App Group |

两份描述文件分别嵌入为主应用和扩展的 `embedded.provisionprofile`，签名时启用 hardened runtime。

### 机密的保管

证书的 `.p12` 及其密码、两份描述文件、`.p8` 私钥都不进仓库，也不贴到对话里。发布 CI 从 GitHub 的 `release`
Environment 读取它们；这个 Environment 只允许 `main` 分支和 `v*` 标签部署，每次部署都要 Ember-Moth 批准。

| 名称 | 类型 | 状态 |
| --- | --- | --- |
| `APPLE_TEAM_ID` | 变量 | 已配置：`GY9ZKZMAZA` |
| `MACOS_CERTIFICATE_NAME` | 变量 | 已配置：`Developer ID Application: Herta Network LLC (GY9ZKZMAZA)`，证书建好后核对 |
| `APP_STORE_CONNECT_KEY_ID` | 变量 | 已配置：`2QSRW72GKA`（Herta Network LLC 的 Team Key，Developer 权限） |
| `APP_STORE_CONNECT_ISSUER_ID` | 变量 | 已配置 |
| `APP_STORE_CONNECT_API_KEY` | 机密 | 已配置 |
| `KEYCHAIN_PASSWORD` | 机密 | 已配置（随机生成） |
| `MACOS_CERTIFICATE_BASE64`、`MACOS_CERTIFICATE_PASSWORD` | 机密 | 已配置：G2 证书，SHA-1 `F462D4E8…FC33`，2027-11-29 到期 |
| `MACOS_APP_PROFILE_BASE64`、`MACOS_NETWORK_EXTENSION_PROFILE_BASE64` | 机密 | 已配置：`Solaris Developer ID`、`Solaris Tunnel Developer ID`（2026-10-30），都授权上面这张证书 |

`.github/workflows/release.yml` 读取它们（见“发布工作流”）。它和 `build_app.py` 一起校验证书、Team、有效期，
以及两份描述文件的权限：主应用必须有 `system-extension.install`，扩展必须有
`packet-tunnel-provider-systemextension`。
