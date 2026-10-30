# Solaris

mihomo 的 Windows / macOS 桌面客户端（PySide6 + Nuitka）。内核以 Go 库的形式内嵌：Windows 上运行在
LocalSystem 服务里，macOS 上运行在 NetworkExtension 系统扩展里，关闭界面后隧道继续运行。

现状：内核、控制协议、Windows 服务与安装器、macOS 系统扩展和界面的七个页面都已写好，界面支持中英双语、浅色与深色；
两个平台都还没有真机实测，也还没有发布。进度和下一步见 [docs/roadmap.md](docs/roadmap.md)。产品名、应用名都是 Solaris，产品标识集中在
`src/core/internal/product/product.go` 与 `src/solaris/product.py`。

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

会离开本机的数据见 [PRIVACY.md](PRIVACY.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | 进度、下一步计划、决策与风险 |
| [docs/architecture.md](docs/architecture.md) | 系统架构：engine、控制协议、Windows 服务、macOS 扩展、安全模型、构建与测试 |
| [docs/ui.md](docs/ui.md) | Python 界面：宿主接口、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为、分流页 |
| [docs/storage.md](docs/storage.md) | SQLite 存储与备份（Google Drive 未做） |
| [docs/routing.md](docs/routing.md) | 自定义分流 |
| [docs/exits.md](docs/exits.md) | 链式出口：经用户节点的 WARP（已实现）、Tor 与 VPN Gate（计划中） |
| [docs/mesh.md](docs/mesh.md) | 组网出站：Tailscale、ZeroTier、EasyTier（应用内组网计划中） |
| [docs/release.md](docs/release.md) | 发布与更新：发布工作流、检查更新、诊断导出、第三方许可证、一键导入 |
| [PRIVACY.md](PRIVACY.md) | 隐私说明：会离开本机的数据、备份与诊断信息 |
| [DESIGN.md](DESIGN.md) | 视觉规范与文案规则 |

## 目录

| 路径 | 内容 |
| --- | --- |
| `proto/` | 控制协议 schema 与 protoc 版本锁 |
| `src/core/` | Go 模块：`internal/` 是控制分发器、帧编解码等共享代码；`service/` 是 Windows 服务入口；`apple/` 编成 macOS 扩展用的 C archive |
| `src/solaris/` | Python UI：宿主客户端、models、viewmodels、QML |
| `apple/` | macOS 管理库与系统扩展（XcodeGen 工程，见 apple/README.md） |
| `package/` | Nuitka 入口、图标、许可证文本、Windows 安装器 |
| `scripts/` | 代码生成（协议、主题、翻译、图标、许可证）、构建与打包、开发工具 |
| `testdata/` | Go 与 Python 共用的协议样本 |

## 开发

需要 Python 3.14、[uv](https://docs.astral.sh/uv/) 与 Go 1.26 以上。

```bash
uv sync
```

修改 `proto/` 后重新生成 Go 与 Python 绑定（protoc 按 `proto/toolchain.json` 下载并校验）：

```bash
uv run python scripts/gen_protocol.py
```

修改 [DESIGN.md](DESIGN.md) 的 token 后重新生成 `src/solaris/ui/components/Theme.qml`：

```bash
uv run python scripts/gen_theme.py
```

改动界面文案后更新翻译目录并编译（源文是中文，英文译文在 `src/solaris/translations/en.ts`，
[ui.md](docs/ui.md) §5）：

```bash
uv run python scripts/gen_i18n.py
```

运行测试：

```bash
uv run pytest
```

```bash
go -C src/core test ./...
```

在 Mac 上联调服务：先以开发模式启动服务（监听本地 socket），再用脚本发一次 `GetInfo`。开发模式以普通用户
运行，不创建 TUN、不改系统代理、路由和 DNS；需要 TUN 的测试只在 CI 或测试虚拟机里做
（[architecture.md](docs/architecture.md) §11）。

```bash
go -C src/core run ./service -socket /tmp/solaris-dev.sock
```

```bash
uv run python scripts/control_ping.py --socket /tmp/solaris-dev.sock
```

用模拟宿主和示例数据预览界面（不需要服务）。界面语言和外观跟随系统，可以用 `--lang en|zh`、`--appearance
light|dark` 指定；`--development` 模拟开发服务拒绝 TUN 和系统代理，`--crowd 2000` 模拟大量连接：

```bash
uv run python scripts/preview.py
```

在 macOS 和 Linux 上让界面连开发服务：脚本构建并启动开发服务，配置和设置放在临时目录（`--data` 可指定目录，
保留到下次），不读写用户数据目录（macOS 上是 `~/Library/Application Support/Solaris/`），退出界面时停止
服务。支持 `--lang`、`--appearance`：

```bash
uv run python scripts/dev.py
```

启动界面并连接真实服务（Windows 上是已安装的服务；其他平台需设置 `SOLARIS_DEV_SOCKET` 指向开发服务；
配置和设置在用户数据目录里）：

```bash
uv run solaris
```

端到端测试让界面连真实的开发服务，经混合端口访问本机起的 HTTP 服务，不需要 TUN，也不改本机网络
（[architecture.md](docs/architecture.md) §11）。默认跳过，这样运行（测试自己构建服务；
`SOLARIS_E2E_SERVICE` 可指向已构建的服务，`SOLARIS_E2E_SCREENSHOTS` 可指向保存截图的目录）：

```bash
SOLARIS_E2E=1 uv run pytest tests/e2e
```

## 许可证

本项目以 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）发布。Windows 服务和 macOS 系统扩展
链接了同为 GPL-3.0 的 [mihomo](https://github.com/MetaCubeX/mihomo)，分发二进制时须同时提供对应的源代码。
