# Solaris

Solaris 是基于 [mihomo](https://github.com/MetaCubeX/mihomo) 的网络代理客户端，支持 Windows x64 和 Apple 芯片的
macOS。内核以 Go 库的形式内嵌：Windows 上运行在系统服务里，macOS 上运行在 NetworkExtension 系统扩展里，关闭界面后
连接继续运行。界面用 PySide6（Qt Quick）编写，以 Nuitka 打包。

## 功能

- **订阅与配置**：导入 mihomo（Clash）订阅链接或本地配置文件，订阅按时更新；网页上的 `solaris://`、`clash://` 链接
  一键导入；一份全局覆写，加上每份订阅各一份覆写。
- **连接**：界面上只有一个连接开关；用 TUN 还是系统代理接管流量，在设置页的"接管方式"里选，TUN 协议栈也可以选。
- **自定义分流**：可以直接用订阅自带的规则，也可以用"我的方案"：所有订阅的节点合成一个节点池，按地区自动分组；
  按服务、域名或 IP 段添加规则；自建策略组并预览成员；用规则测试查看一个网址会走哪里；方案可以导入导出。
- **链式出口**：流量经你的节点再进入 Cloudflare WARP 或 VPN Gate，换一个出口 IP。
- **组网**：在应用里添加 Tailscale、ZeroTier、EasyTier 网络，用规则访问网络里的设备。
- **代理、连接与日志**：策略组与节点选择、延迟测试，实时的连接列表与流量，内核日志。
- **备份**：导出到文件，从文件恢复（替换或合并）；macOS 上可以自动备份到 iCloud。
- 简体中文和英文，浅色与深色外观，托盘，登录时打开，检查更新，诊断信息导出。

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

## 从源码构建

需要 Python 3.14、[uv](https://docs.astral.sh/uv/) 和 Go 1.26 以上。安装包在对应的系统上构建：

- **macOS**：另需 Xcode 和 XcodeGen（`brew install xcodegen`）。产物是 `build/app/Solaris.app`：

  ```bash
  uv run --with nuitka==4.2.2 python scripts/build_app.py --unsigned
  ```

  不带 `--unsigned` 时用钥匙串里的 Developer ID 证书和描述文件签名。不签名的 app 只能查看，不能激活系统扩展。签名、
  公证和工程结构见 [apple/README.md](apple/README.md)。
- **Windows**：另需 Visual Studio 的 C++ 生成工具（MSVC）和 NSIS 3.08 以上。产物是
  `build/windows/Solaris-<版本>-windows-x64-setup.exe`：

  ```bash
  uv run --with nuitka==4.2.2 python scripts/build_windows.py
  ```

  构建步骤见 [docs/architecture.md](docs/architecture.md) §10。

两个脚本都会先下载钉住版本的 GEO 数据（`scripts/fetch_geodata.py`）。发布用的安装包由 GitHub Actions 的发布工作流
构建（[docs/release.md](docs/release.md)）。

## 开发

```bash
uv sync
```

运行测试：

```bash
uv run pytest
```

```bash
go -C src/core test ./...
```

端到端测试让界面连真实的开发服务，经混合端口访问本机起的 HTTP 服务。默认跳过，这样运行（测试自己构建服务；
`SOLARIS_E2E_SERVICE` 可指向已构建的服务，`SOLARIS_E2E_SCREENSHOTS` 可指向保存截图的目录）：

```bash
SOLARIS_E2E=1 uv run pytest tests/e2e
```

CI 还运行 `uv run ruff check .`、`uv run ruff format --check .` 和 `uv run pyright`。

**开发服务**：Windows 服务的二进制在 macOS 和 Linux 上可以作为前台进程运行，以普通用户身份监听一个本地 socket，
不创建 TUN，不改系统代理、路由和 DNS（[docs/architecture.md](docs/architecture.md) §11）。`scripts/dev.py` 一步构建
并启动它，再打开界面；配置和设置放在临时目录（`--data` 可指定目录，保留到下次），不读写用户数据目录，退出界面时
停止服务。支持 `--lang en|zh`、`--appearance light|dark`：

```bash
uv run python scripts/dev.py
```

也可以分开启动：先运行开发服务，再让界面经 `SOLARIS_DEV_SOCKET` 连它（这时配置和设置在用户数据目录里）：

```bash
go -C src/core run ./service -socket /tmp/solaris-dev.sock
```

```bash
SOLARIS_DEV_SOCKET=/tmp/solaris-dev.sock uv run solaris
```

**预览**：用模拟宿主和示例数据预览界面，不需要服务。可以用 `--lang en|zh`、`--appearance light|dark` 指定语言和外观；
`--development` 模拟开发服务拒绝 TUN 和系统代理，`--crowd 2000` 模拟大量连接：

```bash
uv run python scripts/preview.py
```

**生成的文件**：修改 `proto/` 后重新生成 Go 与 Python 的协议绑定，修改 [DESIGN.md](DESIGN.md) 的 token 后重新生成
`Theme.qml`，改动界面文案后更新翻译目录（源文是中文，英文在 `src/solaris/translations/en.ts`）。CI 用 `--check`
核对它们是最新的：

```bash
uv run python scripts/gen_protocol.py
```

```bash
uv run python scripts/gen_theme.py
```

```bash
uv run python scripts/gen_i18n.py
```

需要 TUN 的测试只在 CI 的网络命名空间或测试机里做，不在开发机上建 TUN。工作规则（协议、视觉、文案、表结构的改法，
依赖升级）见 [docs/maintenance.md](docs/maintenance.md) 第 5 节。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/maintenance.md](docs/maintenance.md) | 现状、真机验收、已知问题、发布步骤与工作规则 |
| [docs/decisions.md](docs/decisions.md) | 设计决策（D1–D29） |
| [docs/architecture.md](docs/architecture.md) | 系统架构：engine、控制协议、Windows 服务、macOS 扩展、安全模型、构建与测试 |
| [docs/ui.md](docs/ui.md) | 界面：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为、分流页 |
| [docs/storage.md](docs/storage.md) | 本地数据库与备份：文件、iCloud；Google Drive（未实现） |
| [docs/routing.md](docs/routing.md) | 自定义分流 |
| [docs/exits.md](docs/exits.md) | 链式出口：WARP、VPN Gate；Tor（未实现） |
| [docs/mesh.md](docs/mesh.md) | 组网：Tailscale、ZeroTier、EasyTier |
| [docs/release.md](docs/release.md) | 发布与更新：发布工作流、检查更新、诊断信息、第三方许可证、一键导入 |
| [apple/README.md](apple/README.md) | macOS 原生部分：管理库、系统扩展、签名与公证、iCloud |
| [DESIGN.md](DESIGN.md) | 视觉规范与文案规则 |
| [PRIVACY.md](PRIVACY.md) | 隐私说明 |

## 目录

| 路径 | 内容 |
| --- | --- |
| `proto/` | 控制协议 schema 与 protoc 版本锁 |
| `src/core/` | Go 模块：`internal/` 是 engine、控制分发器、帧编解码等；`service/` 是 Windows 服务入口；`apple/` 编成 macOS 扩展用的 C archive |
| `src/solaris/` | Python 界面：宿主客户端、models、viewmodels、QML |
| `apple/` | macOS 管理库与系统扩展（XcodeGen 工程） |
| `package/` | Nuitka 入口、图标、许可证文本、Windows 安装器 |
| `scripts/` | 代码生成（协议、主题、翻译、图标、许可证）、构建与打包、开发工具 |
| `tests/` | Python 测试；`tests/e2e` 是端到端测试 |
| `testdata/` | Go 与 Python 共用的协议样本 |

## 许可证

本项目以 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）发布。Windows 服务和 macOS 系统扩展
链接了同为 GPL-3.0 的 [mihomo](https://github.com/MetaCubeX/mihomo)，分发二进制时须同时提供对应的源代码。
