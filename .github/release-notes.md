## 更新内容

<!-- 发布前在草稿里写这个版本的变化，再删掉这一行。GitHub 把正文里的换行显示为换行，段落不要折行。 -->

## 下载

| 系统 | 文件 |
| --- | --- |
| Windows 10 / 11，x64 | `Solaris-{{version}}-windows-x64-setup.exe` |
| Windows 11，ARM64 | `Solaris-{{version}}-windows-arm64-setup.exe` |
| macOS 26 及以上，Apple 芯片 / Intel x64 | `Solaris-{{version}}-macos-universal.dmg` |

`SHA256SUMS` 是本页每个文件的 SHA-256。已安装的 Solaris 在设置页的"关于"里检查更新，下载后核对校验和再打开安装包。

- **Windows**：安装包由 Herta Network LLC 签名（Azure Artifact Signing），管理员权限确认框里的发布者是 Herta Network LLC。签名还在积累 SmartScreen 信誉时，从浏览器下载后打开可能仍提示"Windows 已保护你的电脑"：点"更多信息"，确认发布者是 Herta Network LLC，再点"仍要运行"。
- **macOS**：打开磁盘映像，把 Solaris 拖到"应用程序"文件夹。第一次连接时，系统会请求允许 Solaris 的系统扩展和 VPN 配置，按提示在"系统设置"里允许。

## 源代码与许可

这个版本的源代码是本仓库的 [`v{{version}}`](https://github.com/Ember-Moth/Solaris/tree/v{{version}}) 标签。服务和扩展链接的 mihomo 是 [Ember-Moth/mihomo](https://github.com/Ember-Moth/mihomo) 的一个提交，由这个标签的 [`src/core/go.mod`](https://github.com/Ember-Moth/Solaris/blob/v{{version}}/src/core/go.mod) 里的 `replace` 固定；其他 Go 依赖的版本和校验和由同一目录的 `go.mod` 和 `go.sum` 固定，在 `src/core` 里运行 `go mod download` 即可从 Go 模块代理（默认 `proxy.golang.org`）取得它们和 mihomo。GitHub 给每个 Release 自动附带的"Source code"压缩包只有本仓库的代码，不含 mihomo 和其他 Go 依赖。

Solaris 以 GPL-3.0 发布，第三方组件的许可证在设置页的"开源许可"里。会离开本机的数据见[隐私说明](https://github.com/Ember-Moth/Solaris/blob/v{{version}}/PRIVACY.md)。
