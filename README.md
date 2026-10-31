<p align="center">
  <img src="src/solaris/ui/brand/app-icon-macos.svg" width="128" height="128" alt="Solaris">
</p>

<h1 align="center">Solaris</h1>

<p align="center">基于 <a href="https://github.com/MetaCubeX/mihomo">mihomo</a> 的网络代理客户端，支持 Windows 和 macOS。</p>

<p align="center">
  <a href="https://github.com/Ember-Moth/Solaris/releases">下载</a> ·
  <a href="PRIVACY.md">隐私说明</a> ·
  <a href="docs/development.md">开发文档</a>
</p>

---

Solaris 把 mihomo 内核装进系统服务（Windows）或系统扩展（macOS）里运行：关闭界面后连接照常工作；Windows 上重新登录
后自动恢复连接。
界面是原生风格的桌面应用，提供简体中文和英文，跟随系统的浅色与深色外观。

## 功能

- **订阅与配置**：导入 mihomo（Clash）订阅链接或本地配置文件，订阅按时更新；网页上的 `solaris://`、`clash://` 链接
  一键导入；一份全局覆写，加上每份订阅各一份覆写。
- **一个开关连接**：用 TUN 还是系统代理接管流量，在设置里选一次；TUN 协议栈也可以选。
- **自定义分流**：可以直接用订阅自带的规则，也可以用"我的方案"：所有订阅的节点合成一个节点池，按地区自动分组；
  按服务、域名或 IP 段添加规则；自建策略组并预览成员；用规则测试查看一个网址会走哪里；方案可以导入导出。
- **链式出口**：流量经你的节点再进入 Cloudflare WARP 或 VPN Gate，换一个出口 IP。
- **组网**：在应用里添加 Tailscale、ZeroTier、EasyTier 网络，用规则访问网络里的设备。
- **代理、连接与日志**：策略组与节点选择，HTTP 或 TCP 握手两种延迟测试，实时的连接列表与流量，内核日志。
- **备份**：导出到文件，从文件恢复（替换或合并）；自动备份到 Google Drive，macOS 上还可以备份到 iCloud。
- **日常使用**：托盘，登录时打开，检查更新，诊断信息导出。

## 下载与安装

安装包在 [GitHub Releases](https://github.com/Ember-Moth/Solaris/releases)，只提供下面两种：

| 系统 | 文件 |
| --- | --- |
| Windows 10 / 11，64 位 | `Solaris-<版本>-windows-x64-setup.exe` |
| macOS 26 及以上，Apple 芯片 | `Solaris-<版本>-macos-arm64.dmg` |

每个版本附带 `SHA256SUMS`，可以用它核对下载的文件。

- **Windows**：安装包暂未签名。从浏览器下载后打开时，SmartScreen 可能提示"Windows 已保护你的电脑"，点"更多信息"再点
  "仍要运行"。安装器会请求管理员权限，确认框里的发布者显示为"未知"。安装位置固定为 `C:\Program Files\Solaris`，
  同时安装一个系统服务，关闭界面后连接继续运行。在"设置 › 应用"里卸载，卸载时可以选择是否删除订阅、设置和日志。
- **macOS**：打开磁盘映像，把 Solaris 拖到"应用程序"文件夹。第一次连接时，系统会先提示 Solaris 想要启用系统扩展，
  到"系统设置 › 通用 › 登录项与扩展"里允许；再询问是否允许添加 VPN 配置，点"允许"。磁盘映像经过 Apple 公证。
- **更新**：设置页"关于"里检查更新，下载安装包并核对校验和（macOS 还核对签名），经你确认后打开安装包完成升级。

## 隐私

Solaris 不收集使用数据。会离开本机的只有你使用的功能本身需要的连接，比如下载订阅、检查更新、注册 WARP、备份到
你选的云端；逐项说明见 [PRIVACY.md](PRIVACY.md)。

## 参与开发

从源码构建、运行测试和开发服务，以及各部分的设计文档，见 [docs/development.md](docs/development.md)。

## 许可证

本项目以 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）发布。Windows 服务和 macOS 系统扩展链接了同为
GPL-3.0 的 [mihomo](https://github.com/MetaCubeX/mihomo)，分发二进制时须同时提供对应的源代码。
