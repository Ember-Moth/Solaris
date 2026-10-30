# macOS 原生部分

Xcode 工程由 XcodeGen 从 `project.yml` 生成，包含两个 target（docs/architecture.md §6）：

| target | 产物 | 职责 |
| --- | --- | --- |
| SolarisManager | `SolarisManager.dylib`，C ABI | 激活和替换系统扩展、配置 `NETunnelProviderManager`、启停隧道、查询状态、用 `sendProviderMessage` 收发控制消息 |
| SolarisTunnel | `dev.tschen.solaris.tunnel.systemextension` | `NEPacketTunnelProvider`：设置隧道网络和 DNS、取得 utun fd、运行内核；`handleAppMessage` 把消息交给 Go 分发器 |

只构建 arm64（D2），最低系统版本 macOS 15.0。

## 工程结构

```text
apple/
├── project.yml                      XcodeGen 定义；生成的 Solaris.xcodeproj 不提交
├── App/Solaris.entitlements         主应用（Nuitka 产出的 .app）的 entitlement，由脚本签上
├── Manager/                         SolarisManager.dylib
│   ├── SolarisManager.h             C 接口，见下文
│   ├── module.modulemap             让 Swift 导入头文件，逐个检查导出函数的签名
│   ├── Exports.swift                @_cdecl 导出
│   ├── ExtensionActivation.swift    OSSystemExtensionRequest 的激活与替换
│   ├── TunnelController.swift       VPN 配置、启停、状态、provider message
│   └── ManagerError.swift
└── Tunnel/                          dev.tschen.solaris.tunnel.systemextension
    ├── Info.plist                   NEProviderClasses、NEMachServiceName
    ├── SolarisTunnel.entitlements
    ├── main.swift                   NEProvider.startSystemExtensionMode()
    ├── PacketTunnelProvider.swift   startTunnel、stopTunnel、handleAppMessage
    ├── TunnelAddresses.swift        隧道地址、路由、DNS、MTU
    ├── TunnelDescriptor.swift       在打开的描述符里找 utun
    └── ControlDispatcher.swift      调 solaris_handle_message
```

生成的 `Solaris.xcodeproj` 不提交（已在 .gitignore）。`project.yml` 是唯一的来源：pbxproj 是生成物，
提交了就要防着两者不一致，还要在 CI 里重新生成再比对，结果仍离不开 XcodeGen；pbxproj 的改动也难以审阅、
容易冲突。生成只要一秒，做法与 hypervpn 相同。

## 构建

需要 Xcode、XcodeGen（`brew install xcodegen`）和 Go，在仓库根目录运行：

```bash
uv run python scripts/build_apple.py
```

```bash
uv run python scripts/build_apple.py --sign
```

不带参数是不签名构建（`CODE_SIGNING_ALLOWED=NO`），给 CI 和没有证书的机器用；`--sign` 用本机钥匙串里的
Developer ID 证书签名并校验。脚本依次：

1. 把 `src/core/apple` 编成 arm64 的 C archive（`GOPROXY=https://goproxy.cn,direct`），连同 cgo 生成的
   `libsolaris.h` 和一份 module map 包成 `SolarisCore.xcframework`。module map 把模块命名为 `SolarisCore`，
   并自动链接 Go 的 `net` 包要用的 `libresolv`；
2. 用 XcodeGen 生成工程，`xcodebuild` 构建 Release；
3. 检查产物：只含 arm64，dylib 导出的函数与头文件一致，扩展的 Info.plist 正确；
4. 组装一个检查用的 `Solaris.app`（见“签名”），`--sign` 时签名并校验。

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
- `--tags` 传 Go 构建标签；扩展用哪组标签在 M5 定（docs/architecture.md §3.4），默认不带。
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

主应用最终是 Nuitka 产出的 .app，签名放在 M5 的“组装 .app”一步。在那之前，脚本用检查用的
`check/Solaris.app` 验证同一条路径：把 dylib 放进 `Contents/Frameworks`、扩展放进
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

## C 接口（`Manager/SolarisManager.h`）

| 函数 | 作用 |
| --- | --- |
| `solaris_manager_activate(timeout, &error)` | 激活包内的系统扩展，替换其他构建激活的扩展 |
| `solaris_manager_configure(&error)` | 创建或修复 VPN 配置并启用；第一次保存时系统请用户允许 |
| `solaris_manager_start(timeout, &error)` | 必要时先配置，再启动隧道，等到已连接 |
| `solaris_manager_stop(timeout, &error)` | 停止隧道，等到断开；本来就没运行时直接成功 |
| `solaris_manager_status(&status, &error)` | 隧道状态：`SOLARIS_TUNNEL_*`，对应 `NEVPNStatus`，另加“未配置” |
| `solaris_manager_send(request, length, timeout, &response, &response_length, &error)` | 发一条编码好的 Request，取回 Response |
| `solaris_manager_free(pointer)` | 释放本库交出的字符串或缓冲区 |

约定：

- **返回值**：除 `solaris_manager_free` 外都返回 `SolarisManagerResult`（`int32_t`）：`OK`、`FAILED`、
  `TIMED_OUT`、`NOT_CONFIGURED`（还没有 VPN 配置）、`NOT_CONNECTED`（隧道未连接，扩展没在运行）、
  `NEEDS_APPROVAL`（等用户在系统设置里允许扩展）、`NEEDS_REBOOT`（重启后完成激活）。
- **错误信息**：结果不是 `OK` 且 `error` 不为 NULL 时，`*error` 是一段 UTF-8 说明；否则置为 NULL。
- **内存**：传入的缓冲区归调用方所有，只在调用期间读取；经指针参数交出的字符串和缓冲区归调用方，
  用 `solaris_manager_free` 释放（空的 Response 也是一块要释放的缓冲区）。
- **线程**：调用会阻塞到完成或超时。NetworkExtension 在主队列上完成回调，所以要在后台线程调用，并且
  主线程在跑 run loop（Qt 的事件循环就是）；在主线程调用会直接返回 `FAILED`。`configure`、`start`、
  `stop` 依次执行，`status` 和 `send` 可以与它们并行。
- **激活**：系统要用户批准时，`activate` 立即返回 `NEEDS_APPROVAL`，请求继续挂着；之后的调用等同一个请求，
  用户允许后返回 `OK`，到超时仍未允许则再返回 `NEEDS_APPROVAL`。替换时总是用包内的扩展，因为只有它和
  这个 app 的控制协议一致。
- **配置**（D7）：`start` 不带任何配置；连接后，宿主用 `send` 发 `Start` / `Reload`。
- Swift 侧导入同一份头文件，编译时逐个比对导出函数的签名；构建脚本再核对 dylib 实际导出的符号。

预期的调用顺序：`activate`（`NEEDS_APPROVAL` 时提示用户并继续调用）→ `start` → `send(Start)` →
`send(...)` → `stop`。

## Go 归档

`src/core/apple` 导出两个函数（cgo 生成 `libsolaris.h`）：`solaris_handle_message` 处理一条编码好的 Request
并返回 Response，返回的缓冲区由调用方用 `solaris_free` 释放。扩展在并发队列上调用它，状态变化由分发器内部
的操作锁串行。Swift 只转发字节，不解析消息。

## 尚未完成

- **engine 的 fd 模式**（Go）：`startTunnel` 设好网络、找到 utun fd 后，目前以“fd 模式尚未实现”失败，
  `PacketTunnelProvider.swift` 里留了 TODO。实现时要从 `src/core/apple` 再导出一个接收 fd 的函数，
  engine 的 TUN 地址要与 `TunnelAddresses.swift` 一致。
- **Python 侧的 `MacExtensionHost`**：通过 ctypes 调用管理库。
- **组装 .app**：把 Nuitka 产物、管理库和扩展装进 `Solaris.app`，按上面的方式签名。
- **公证**和发布 CI。
- **测试 Mac 上的实测**：激活、批准、连接、provider message 的大小上限（D8）。
- 卸载前停用扩展（`deactivationRequest`）、扩展说明文字的本地化。

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
| 主应用 | `com.apple.developer.system-extension.install`；`com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；`com.apple.security.application-groups` 含 `group.dev.tschen.solaris`；iCloud 的 CloudKit 容器 `iCloud.dev.tschen.solaris` |
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

签名任务校验证书、Team、有效期，以及两份描述文件的权限：主应用必须有 `system-extension.install`，扩展必须有
`packet-tunnel-provider-systemextension`。
