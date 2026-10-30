# Solaris

mihomo 的 Windows / macOS 桌面客户端（PySide6 + Nuitka）。内核以 Go 库的形式内嵌：Windows 上运行在
LocalSystem 服务里，macOS 上运行在 NetworkExtension 系统扩展里，关闭界面后隧道继续运行。

现状：内核（engine、控制协议、开发模式服务）和界面的六个页面都已实现，界面支持中英双语、浅色与深色，并已与真实的
开发服务端到端联调；Windows 服务产品化、macOS 系统扩展、SQLite 存储与云备份、自定义分流还在计划中。进度和下一步见
[docs/roadmap.md](docs/roadmap.md)。产品名、应用名都是 Solaris，产品标识集中在
`src/core/internal/product/product.go` 与 `src/solaris/product.py`。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/roadmap.md](docs/roadmap.md) | 进度、下一步计划、决策与风险 |
| [docs/architecture.md](docs/architecture.md) | 系统架构：engine、控制协议、Windows 服务、macOS 扩展、安全模型、构建与测试 |
| [docs/ui.md](docs/ui.md) | Python 界面：宿主接口、配置存储、订阅、覆写、多语言、外观、连接与日志、设置与桌面行为 |
| [docs/storage.md](docs/storage.md) | 计划中的 SQLite 存储与云备份 |
| [docs/routing.md](docs/routing.md) | 计划中的自定义分流 |
| [docs/exits.md](docs/exits.md) | 计划中的链式出口：经用户节点的 WARP、Tor 与 VPN Gate |
| [docs/mesh.md](docs/mesh.md) | 计划中的组网出站：Tailscale、ZeroTier、EasyTier |
| [DESIGN.md](DESIGN.md) | 视觉规范 |

## 目录

| 路径 | 内容 |
| --- | --- |
| `proto/` | 控制协议 schema 与 protoc 版本锁 |
| `src/core/` | Go 模块：`internal/` 是控制分发器、帧编解码等共享代码；`service/` 是 Windows 服务入口；`apple/` 编成 macOS 扩展用的 C archive |
| `src/solaris/` | Python UI：宿主客户端、models、viewmodels、QML |
| `apple/` | macOS 管理库与系统扩展（待建 Xcode 工程） |
| `package/` | Nuitka 入口与打包脚本 |
| `scripts/` | 协议代码生成、开发工具 |
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
