<p align="center">
  <img src="src/solaris/ui/brand/app-icon-macos.svg" width="128" height="128" alt="Solaris">
</p>

<h1 align="center">Solaris</h1>

<p align="center"><strong>让 mihomo 融入操作系统。</strong></p>

<p align="center">采用平台原生系统架构的开源网络代理客户端，支持 macOS 和 Windows。</p>

<p align="center">
  <a href="https://github.com/Ember-Moth/Solaris/releases">下载</a> ·
  <a href="#原生系统集成">产品特点</a> ·
  <a href="PRIVACY.md">隐私说明</a> ·
  <a href="https://github.com/Ember-Moth/Solaris/issues">反馈问题</a> ·
  <a href="docs/development.md">开发文档</a>
</p>

---

Solaris 用来管理你的代理订阅、节点和分流规则。它在 macOS 上使用 **Network Extension**，在 Windows 上使用
**LocalSystem 系统服务**，将 mihomo 内核直接嵌入系统扩展或服务中。

界面负责管理配置，系统扩展或服务负责运行连接。连接建立后，即使退出界面，隧道仍可继续运行。无需注册 Solaris 账户，导入自己的
订阅或配置即可使用。

## 原生系统集成

Solaris 使用两个平台各自提供的权限和后台运行机制来管理网络连接。

| 项目 | macOS | Windows |
| --- | --- | --- |
| 网络宿主 | Network Extension 系统扩展 | 原生 Windows Service，以 LocalSystem 身份运行 |
| 内核运行 | mihomo 以 Go 库形式直接内嵌 | mihomo 以 Go 库形式直接内嵌 |
| 界面控制 | Network Extension 提供的消息通道 | Windows 命名管道 |
| 系统集成 | 系统扩展授权、VPN 配置与隧道生命周期 | 服务管理、用户会话与电源事件 |

**内核独立于界面运行。** mihomo 直接运行在系统扩展或服务进程内，不作为独立 CLI 子进程托管。
界面通过平台 IPC 和自有控制协议调用内核，无需开放 mihomo 的 HTTP/REST 控制端口。

**权限分工明确。** 日常使用的界面以普通用户权限运行，需要系统权限的网络操作交给系统扩展或服务处理。
Windows 服务按用户管理连接，处理登录与睡眠唤醒后的恢复；主动断开后不会自动重连。
macOS 在首次连接时通过系统授权启用扩展，也提供卸载前的扩展停用入口。

控制通道、内核集成与权限边界的详细设计见 [架构文档](docs/architecture.md)。

## 连接与分流

- **一个开关连接**：Windows 可选择 TUN、系统代理或同时启用；macOS 通过 Network Extension 接管流量。
  在概览页查看连接状态、当前模式和实时流量。
- **集中管理订阅**：导入 mihomo（Clash）订阅链接或本地配置，设置定时更新，通过 `solaris://`、`clash://` 链接
  一键导入。支持全局覆写和单份订阅覆写。
- **使用订阅规则，或建立自己的方案**：把多个订阅的节点合成节点池，按地区组织，自建策略组；按服务、域名和
  IP 段设置分流。规则测试可以预览目标网站会走哪条规则，方案支持导入与导出。
- **选择与排查节点**：切换规则、全局和直连模式，选择策略组与节点，进行 HTTP 或 TCP 握手延迟测试，查看连接列表
  和内核日志。

## 更多网络能力

Solaris 使用 [Ember-Moth/mihomo](https://github.com/Ember-Moth/mihomo) 内核，在 mihomo 的协议能力上加入：

- **Naive**：支持 HTTP/2、QUIC，以及兼容服务端上的 UoT。
- **AnyTLS + REALITY**：支持使用 REALITY 的 AnyTLS 节点。
- **Snell v4–v6**：支持用户密钥和连接复用，v6 可选择与服务端一致的传输模式。

节点怎么配置、哪些参数需要服务端支持，以及完整 YAML 示例，见 [内核扩展与配置指南](docs/mihomo-fork.md)。

此外，可以让流量经所选节点接入 **Cloudflare WARP 或 VPN Gate** 链式出口；也可以添加 **Tailscale、ZeroTier、
EasyTier** 网络，通过分流规则访问网络中的设备。

## 日常使用

- **贴合桌面习惯**：简体中文与英文，浅色与深色外观，托盘控制和登录时打开。
- **备份与迁移**：导出本地数据，恢复时选择替换或合并；iCloud（macOS）和 Google Drive 的集成状态见
  [支持说明](docs/maintenance.md)。
- **应用内更新**：检查新版本、下载安装包并校验，经你确认后安装。
- **本地诊断**：查看运行日志，导出诊断信息；原始崩溃报告单独确认导出，不会自动上传。

## 下载与安装

从 [GitHub Releases](https://github.com/Ember-Moth/Solaris/releases) 获取安装包：

| 支持的平台 | 安装包 |
| --- | --- |
| macOS 26 及以上，Apple Silicon | `Solaris-<版本>-macos-arm64.dmg` |
| Windows 10 / 11，x64 | `Solaris-<版本>-windows-x64-setup.exe` |

发布附件中的 `SHA256SUMS` 可用于核对下载文件。

**macOS**：打开 DMG，将 Solaris 拖入「应用程序」。首次连接时按应用内指引，在「系统设置 › 通用 › 登录项与扩展」
允许网络扩展，并确认添加 VPN 配置。卸载前可以在 Solaris 设置页停用系统扩展。

**Windows**：运行 EXE 安装程序并允许管理员授权，阅读并同意 GPLv3 开源许可协议。默认安装在 `C:\Program Files\Solaris`，
首次安装可选择其他本地文件夹；安装器会检查目录权限，升级沿用已有位置。安装时同时注册后台服务；
日常使用的界面以普通用户身份运行。当前 Windows 安装包尚未签名。卸载入口位于「设置 › 应用」，默认保留订阅、设置
和日志，也可选择清除。

## 隐私

Solaris 不收集使用统计，也不会自动上传日志或崩溃报告。订阅、配置和设置保存在本机；访问订阅服务器、代理节点、
更新服务或所选云备份服务时，会产生对应功能所需的网络请求。

诊断文件可能包含访问过的域名、节点名或错误内容，由你决定是否导出和分享。数据存储、云备份和第三方服务的完整说明
见 [隐私说明](PRIVACY.md)。

## 支持与开发

- 使用问题与功能建议：[GitHub Issues](https://github.com/Ember-Moth/Solaris/issues)。
- 已知限制、云备份接入与平台验证状态：[维护与支持说明](docs/maintenance.md)。
- 从源码构建、运行测试和参与开发：[开发文档](docs/development.md)。

## 许可证

Solaris 以 [GPL-3.0-only](LICENSE) 发布，内核基于 [mihomo](https://github.com/MetaCubeX/mihomo) 及其
[Ember-Moth fork](https://github.com/Ember-Moth/mihomo)。第三方组件及其许可证可在应用的「设置 › 关于 › 开源许可」中查看。

## 赞助与商务合作

如果 Solaris 对你有帮助，欢迎赞助项目的持续开发与维护。

- **TRON**：`TSNLCUnYzasf7zUv932igYkeLDu4DXj5oc`
- **商务合作**：[Telegram · @EmberMoth_bot](https://t.me/EmberMoth_bot)
