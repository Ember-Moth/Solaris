# mihomo-desktop

mihomo 的 Windows / macOS 桌面客户端（PySide6 + Nuitka）。内核以 Go 库的形式内嵌：Windows 上运行在
LocalSystem 服务里，macOS 上运行在 NetworkExtension 系统扩展里。设计见 [docs/architecture.md](docs/architecture.md)，
开发计划见 [docs/roadmap.md](docs/roadmap.md)。

项目处于骨架阶段：控制协议、帧编解码、方法分发和服务入口已经就位，界面已实现概览和代理两页（配合模拟宿主）。
内核层 engine（直接内嵌 mihomo）尚未实现，服务端除 `GetInfo` 外的方法都返回 `not_implemented`。
`mihomo-desktop` 是产品命名前的工作名，集中在
`src/core/internal/product/product.go` 与 `src/mihomo_desktop/product.py`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `proto/` | 控制协议 schema 与 protoc 版本锁 |
| `src/core/` | Go 模块：`internal/` 是控制分发器、帧编解码等共享代码；`service/` 是 Windows 服务入口；`apple/` 编成 macOS 扩展用的 C archive |
| `src/mihomo_desktop/` | Python UI：宿主客户端、models、viewmodels、QML |
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

修改 [DESIGN.md](DESIGN.md) 的 token 后重新生成 `src/mihomo_desktop/ui/components/Theme.qml`：

```bash
uv run python scripts/gen_theme.py
```

改动界面文案后更新翻译目录并编译（源文是中文，英文译文在 `src/mihomo_desktop/translations/en.ts`，
[architecture.md](docs/architecture.md) §7.4）：

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
go -C src/core run ./service -socket /tmp/mihomo-desktop-dev.sock
```

```bash
uv run python scripts/control_ping.py --socket /tmp/mihomo-desktop-dev.sock
```

用模拟宿主和示例数据预览界面（不需要服务；目前实现了概览和代理两页）。界面语言跟随系统，`--lang en` 或
`--lang zh` 可以指定：

```bash
uv run python scripts/preview.py
```

启动界面并连接真实服务（Windows 上是已安装的服务；其他平台需设置 `MIHOMO_DESKTOP_DEV_SOCKET` 指向开发服务）：

```bash
uv run mihomo-desktop
```

## 许可证

本项目以 [GNU General Public License v3.0](LICENSE)（GPL-3.0-only）发布。Windows 服务和 macOS 系统扩展
链接了同为 GPL-3.0 的 [mihomo](https://github.com/MetaCubeX/mihomo)，分发二进制时须同时提供对应的源代码。
