<p align="center">
  <img src="src/solaris/ui/brand/app-icon-macos.svg" width="128" height="128" alt="Solaris">
</p>

<h1 align="center">Solaris</h1>

<p align="center"><strong>让 mihomo 融入操作系统。</strong></p>

<p align="center">采用平台原生系统架构的开源网络代理客户端，支持 macOS 和 Windows。</p>

<p align="center">
  <a href="https://github.com/Ember-Moth/Solaris/releases">下载</a> ·
  <a href="#原生系统集成">产品特点</a> ·
  <a href="docs/usage.md">使用指南</a> ·
  <a href="PRIVACY.md">隐私说明</a> ·
  <a href="https://github.com/Ember-Moth/Solaris/issues">反馈问题</a> ·
  <a href="docs/development.md">开发文档</a>
</p>

---

Solaris 用来管理你的代理订阅、节点和分流规则。无需注册 Solaris 账户，导入自己的订阅或配置即可使用。

## 原生系统集成

- **后台保持连接**：关闭窗口后 Solaris 留在托盘或菜单栏，连接继续运行；「退出 Solaris」会先断开连接再退出。
- **日常使用无需管理员权限**：macOS 首次连接时请求系统扩展和 VPN 授权；Windows 安装时注册后台服务。
- **随系统使用**：支持托盘操作、登录时打开及睡眠唤醒后的连接恢复。主动断开后不会自动重连。

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

Solaris 使用 [mihomo 增强版](https://github.com/Ember-Moth/mihomo)内核，在 mihomo 的协议能力上加入：

- **Naive**：支持 HTTP/2、QUIC，以及兼容服务端上的 UoT。
- **AnyTLS + REALITY**：支持使用 REALITY 的 AnyTLS 节点。
- **Snell v4–v6**：支持用户密钥和连接复用，v6 可选择与服务端一致的传输模式。

节点怎么配置、哪些参数需要服务端支持，以及完整 YAML 示例，见 [mihomo 增强版配置指南](docs/mihomo-fork.md)。

此外，可以让流量经所选节点接入 **Cloudflare WARP 或 VPN Gate** 链式出口；也可以添加 **Tailscale、ZeroTier、
EasyTier** 网络，通过分流规则访问网络中的设备。

## 日常使用

- **贴合桌面习惯**：简体中文与英文，浅色与深色外观，托盘控制和登录时打开。
- **备份与迁移**：导出本地数据，恢复时选择替换或合并；macOS 支持 iCloud 云备份。
  云备份的可用条件见 [使用指南](docs/usage.md#备份与恢复)。
- **应用内更新**：检查新版本、下载安装包并校验，经你确认后安装。
- **本地诊断**：查看运行日志，导出诊断信息；原始崩溃报告单独确认导出，不会自动上传。

## 下载与安装

从 [GitHub Releases](https://github.com/Ember-Moth/Solaris/releases) 获取安装包：

| 支持的平台 | 安装包 |
| --- | --- |
| macOS 26 及以上，Apple Silicon / Intel x64 | `Solaris-<版本>-macos-universal.dmg` |
| Windows 10 / 11，x64 | `Solaris-<版本>-windows-x64-setup.exe` |
| Windows 11，ARM64 | `Solaris-<版本>-windows-arm64-setup.exe` |

发布附件中的 `SHA256SUMS` 可用于核对下载文件。

**macOS**：打开 DMG，将 Solaris 拖入「应用程序」。首次连接时按应用内指引，在「系统设置 › 通用 › 登录项与扩展」
允许网络扩展，并确认添加 VPN 配置。卸载前可以在 Solaris 设置页停用系统扩展。

**Windows**：运行 EXE 安装程序并允许管理员授权，阅读并同意 GPLv3 开源许可协议。默认安装在 `C:\Program Files\Solaris`，
首次安装可选择其他本地文件夹；安装器会检查目录权限，升级沿用已有位置。安装时同时注册后台服务；
日常使用的界面以普通用户身份运行。当前 Windows 安装包尚未签名。卸载入口位于「设置 › 应用」，默认保留订阅、设置
和日志，也可选择清除。

Windows on ARM 使用 ARM64 安装包。应用内更新按系统架构选择安装包；升级沿用已有安装位置并保留用户数据。
首次连接、备份恢复及常见问题见 [使用指南](docs/usage.md)。

## 隐私

Solaris 不收集使用统计，也不会自动上传日志或崩溃报告。订阅、配置和设置保存在本机；访问订阅服务器、代理节点、
更新服务或所选云备份服务时，会产生对应功能所需的网络请求。

诊断文件可能包含访问过的域名、节点名或错误内容，由你决定是否导出和分享。数据存储、云备份和第三方服务的完整说明
见 [隐私说明](PRIVACY.md)。

## 支持与开发

- 使用问题与功能建议：[GitHub Issues](https://github.com/Ember-Moth/Solaris/issues)。
- 安装、连接、备份与常见问题：[使用指南](docs/usage.md)。
- 从源码构建、运行测试和参与开发：[开发文档](docs/development.md)。

## 许可证

Solaris 以 [GPL-3.0-only](LICENSE) 发布，使用基于 [mihomo](https://github.com/MetaCubeX/mihomo) 的
[mihomo 增强版](https://github.com/Ember-Moth/mihomo)内核。第三方组件及其许可证可在应用的「设置 › 关于 › 开源许可」中查看。

## 赞助与商务合作

如果 Solaris 对你有帮助，欢迎赞助项目的持续开发与维护。

- **TRON**：`TSNLCUnYzasf7zUv932igYkeLDu4DXj5oc`
- **商务合作**：[Telegram · @EmberMoth_bot](https://t.me/EmberMoth_bot)
