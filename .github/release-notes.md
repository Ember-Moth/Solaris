## 更新内容

<!-- 发布前在草稿里写这个版本的变化，再删掉这一行。GitHub 把正文里的换行显示为换行，段落不要折行。 -->

## 下载

| 系统 | 文件 |
| --- | --- |
| Windows 10 / 11，64 位 | `Solaris-{{version}}-windows-x64-setup.exe` |
| macOS 26 及以上，Apple 芯片 | `Solaris-{{version}}-macos-arm64.dmg` |

`SHA256SUMS` 是本页每个文件的 SHA-256。已安装的 Solaris 在设置页的"关于"里检查更新，下载后核对校验和再打开安装包。

- **Windows**：安装包暂未签名。从浏览器下载后打开时，SmartScreen 可能提示"Windows 已保护你的电脑"，点"更多信息"再点"仍要运行"；之后的管理员权限确认框里发布者显示为"未知"。
- **macOS**：打开磁盘映像，把 Solaris 拖到"应用程序"文件夹。第一次连接时，系统会请求允许 Solaris 的系统扩展和 VPN 配置，按提示在"系统设置"里允许。

## 源代码与许可

`Solaris-{{version}}-source.tar.gz` 是这个版本的源代码，含全部 Go 依赖。Solaris 以 GPL-3.0 发布，第三方组件的许可证在设置页的"开源许可"里。会离开本机的数据见[隐私说明](https://github.com/Ember-Moth/Solaris/blob/v{{version}}/PRIVACY.md)。
