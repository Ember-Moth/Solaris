# 开发

本文写给参与开发的人：从源码构建、运行测试与开发服务、生成的文件、文档和目录。现状、真机验收和工作规则见
[maintenance.md](maintenance.md)。下面的命令都在仓库根目录运行。

界面用 PySide6（Qt Quick）编写，以 Nuitka 打包；内核是内嵌 mihomo 的 Go 库，Windows 上运行在系统服务里，macOS
上运行在 NetworkExtension 系统扩展里（[architecture.md](architecture.md)）。

## 从源码构建

需要 Python 3.14、[uv](https://docs.astral.sh/uv/) 和 Go 1.26 以上。安装包在对应的系统上构建：

- **macOS**：另需 Xcode 和 XcodeGen（`brew install xcodegen`）。产物是 `build/app/Solaris.app`：

  ```bash
  uv run --with nuitka==4.2.2 python scripts/build_app.py --unsigned
  ```

  不带 `--unsigned` 时用钥匙串里的 Developer ID 证书和描述文件签名。不签名的 app 只能查看，不能激活系统扩展；签名
  但没有公证的 app 也不能激活。签名、公证和工程结构见 [apple/README.md](../apple/README.md)。
- **Windows**：另需 Visual Studio 的 C++ 生成工具（MSVC）和 NSIS 3.08 以上。产物是
  `build/windows/Solaris-<版本>-windows-x64-setup.exe`：

  ```bash
  uv run --with nuitka==4.2.2 python scripts/build_windows.py
  ```

  构建步骤见 [architecture.md](architecture.md) §10。

两个脚本都会先下载钉住版本的 GEO 数据（`scripts/fetch_geodata.py`）。发布用的安装包由 GitHub Actions 的发布工作流
构建（[release.md](release.md)）。

## 测试与开发服务

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

CI 还运行 `uv run ruff check .`、`uv run ruff format --check .`、`uv run pyright` 和 `go test -race ./...`。

**开发服务**：Windows 服务的二进制在 macOS 和 Linux 上可以作为前台进程运行，以普通用户身份监听一个本地 socket，
不创建 TUN，不改系统代理、路由和 DNS（[architecture.md](architecture.md) §11）。`scripts/dev.py` 一步构建并启动它，
再打开界面；配置和设置放在临时目录（`--data` 可指定目录，保留到下次），不读写用户数据目录，退出界面时停止服务。
支持 `--lang en|zh`、`--appearance light|dark`：

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

需要 TUN 的测试只在 CI 的网络命名空间或测试机里做，不在开发机上建 TUN。

## 生成的文件

修改 `proto/` 后重新生成 Go 与 Python 的协议绑定，修改 [DESIGN.md](../DESIGN.md) 的 token 后重新生成 `Theme.qml`，
改动界面文案后更新翻译目录（源文是中文，英文在 `src/solaris/translations/en.ts`）。CI 用 `--check` 核对它们是最新的：

```bash
uv run python scripts/gen_protocol.py
```

```bash
uv run python scripts/gen_theme.py
```

```bash
uv run python scripts/gen_i18n.py
```

协议、视觉、文案、表结构的改法和依赖升级，见 [maintenance.md](maintenance.md) 第 5 节。

## 文档

| 文档 | 内容 |
| --- | --- |
| [maintenance.md](maintenance.md) | 现状、真机验收、已知问题、发布步骤与工作规则 |
| [decisions.md](decisions.md) | 设计决策 |
| [architecture.md](architecture.md) | 系统架构：engine、控制协议、Windows 服务、macOS 扩展、安全模型、构建与测试 |
| [ui.md](ui.md) | 界面：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为、分流页 |
| [storage.md](storage.md) | 本地数据库与备份：文件、Google Drive、iCloud |
| [routing.md](routing.md) | 自定义分流 |
| [exits.md](exits.md) | 链式出口：WARP、VPN Gate；Tor（未实现） |
| [mesh.md](mesh.md) | 组网：Tailscale、ZeroTier、EasyTier |
| [release.md](release.md) | 发布与更新：发布工作流、检查更新、诊断信息、第三方许可证、一键导入 |
| [apple/README.md](../apple/README.md) | macOS 原生部分：管理库、系统扩展、签名与公证、iCloud |
| [DESIGN.md](../DESIGN.md) | 视觉规范与文案规则 |
| [PRIVACY.md](../PRIVACY.md) | 隐私说明 |

## 目录

| 路径 | 内容 |
| --- | --- |
| `proto/` | 控制协议 schema 与 protoc 版本锁 |
| `src/core/` | Go 模块：`internal/` 是 engine、控制分发器、帧编解码等；`service/` 是 Windows 服务入口；`apple/` 编成 macOS 扩展用的 C archive |
| `src/solaris/` | Python 界面：宿主客户端、models、viewmodels、QML |
| `apple/` | macOS 管理库与系统扩展（XcodeGen 工程） |
| `package/` | Nuitka 入口、图标、许可证文本、磁盘映像的背景、Windows 安装器 |
| `scripts/` | 代码生成（协议、主题、翻译、图标、许可证）、构建与打包、开发工具 |
| `tests/` | Python 测试；`tests/e2e` 是端到端测试 |
| `testdata/` | Go 与 Python 共用的协议样本 |
