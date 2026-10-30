# macOS 原生部分

这里将放 Xcode 工程，包含两个 target（docs/architecture.md §6）：

| target | 产物 | 职责 |
| --- | --- | --- |
| Manager | `<Product>Manager.dylib`，C ABI | 激活系统扩展、配置 `NETunnelProviderManager`、启停隧道、用 `sendProviderMessage` 转发控制消息 |
| PacketTunnel | `<bundle-id>.systemextension` | `NEPacketTunnelProvider`：设置隧道网络、取得 utun fd、运行内核；`handleAppMessage` 把消息交给 Go 分发器 |

Go 分发器由 `src/core/apple` 编成 C archive：

```bash
go -C src/core build -buildmode=c-archive -o ../../build/apple/libmihomo_desktop.a ./apple
```

生成的头文件声明两个函数：`mihomo_desktop_handle_message` 处理一条编码好的 Request 并返回 Response，
返回的缓冲区由调用方用 `mihomo_desktop_free` 释放。Swift 只转发字节，不解析消息。

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

### 还要准备（M5 开始前）

- **证书**：Developer ID Application，用来签应用、系统扩展和 DMG。只有 Account Holder 能创建。发 DMG 不需要
  Developer ID Installer。
- **描述文件**：两份 macOS **Developer ID** 类型的描述文件，分别对应两个 App ID，都授权上面那张证书。
  调试时如果不想每次都公证，可以另外登记测试 Mac 的 Provisioning UDID，建两份 macOS App Development 描述文件。
- **公证**：App Store Connect → Users and Access → Integrations → Keys，建一个 Developer 角色的 API Key，
  得到 Key ID、Issuer ID 和 `AuthKey_<KeyID>.p8`。
- **测试机**：一台单独的 Apple Silicon Mac 或 macOS 虚拟机。系统扩展会创建 utun，不在开发机上运行。
- **最低系统版本**：建议 macOS 15（PySide6 绑定库实际的部署版本是 15.0），M5 确认。

### entitlement

| 目标 | entitlement |
| --- | --- |
| 主应用 | `com.apple.developer.system-extension.install`；`com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；`com.apple.security.application-groups` 含 `group.dev.tschen.solaris`；iCloud 的 CloudKit 容器 `iCloud.dev.tschen.solaris` |
| 系统扩展 | `com.apple.developer.networking.networkextension` 含 `packet-tunnel-provider-systemextension`；App Sandbox；`network.client` / `network.server`；同一个 App Group |

两份描述文件分别嵌入为主应用和扩展的 `embedded.provisionprofile`，签名时启用 hardened runtime。

### 机密的保管

证书的 `.p12` 及其密码、两份描述文件、`.p8` 私钥都不进仓库，也不贴到对话里。发布 CI 用 GitHub Actions
Secrets 注入：`APPLE_TEAM_ID`、`MACOS_CERTIFICATE_NAME`、`MACOS_CERTIFICATE_BASE64`、`MACOS_CERTIFICATE_PASSWORD`、
`MACOS_APP_PROFILE_BASE64`、`MACOS_NETWORK_EXTENSION_PROFILE_BASE64`、`KEYCHAIN_PASSWORD`、
`APP_STORE_CONNECT_KEY_ID`、`APP_STORE_CONNECT_ISSUER_ID`、`APP_STORE_CONNECT_API_KEY`。签名任务校验证书、Team、
有效期，以及两份描述文件的权限：主应用必须有 `system-extension.install`，扩展必须有
`packet-tunnel-provider-systemextension`。
