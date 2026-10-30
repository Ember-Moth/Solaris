# macOS 原生部分

这里将放 Xcode 工程，包含两个 target（docs/architecture.md §6）：

| target | 产物 | 职责 |
| --- | --- | --- |
| Manager | `SolarisManager.dylib`，C ABI | 激活系统扩展、配置 `NETunnelProviderManager`、启停隧道、用 `sendProviderMessage` 转发控制消息 |
| PacketTunnel | `dev.tschen.solaris.tunnel.systemextension` | `NEPacketTunnelProvider`：设置隧道网络、取得 utun fd、运行内核；`handleAppMessage` 把消息交给 Go 分发器 |

Go 分发器由 `src/core/apple` 编成 C archive：

```bash
go -C src/core build -buildmode=c-archive -o ../../build/apple/libsolaris.a ./apple
```

生成的头文件声明两个函数：`solaris_handle_message` 处理一条编码好的 Request 并返回 Response，
返回的缓冲区由调用方用 `solaris_free` 释放。Swift 只转发字节，不解析消息。

尚未完成：Xcode 工程、签名与 entitlement、arm64 切片（D2：macOS 先只做 arm64）、engine 的 fd 模式。

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
- **最低系统版本**：建议 macOS 15（PySide6 绑定库实际的部署版本是 15.0），M5 确认。

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
