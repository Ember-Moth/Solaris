# mihomo-desktop

mihomo 的 Windows / macOS 桌面客户端（PySide6 + Nuitka）。内核以 Go 库的形式内嵌：Windows 上运行在
LocalSystem 服务里，macOS 上运行在 NetworkExtension 系统扩展里。设计见 [docs/architecture.md](docs/architecture.md)。

项目处于骨架阶段：控制协议、帧编解码、方法分发和服务入口已经就位，内核（libclash engine）尚未接入，
除 `GetInfo` 外的方法都返回 `not_implemented`。`mihomo-desktop` 是产品命名前的工作名，集中在
`src/core/internal/product/product.go` 与 `src/mihomo_desktop/product.py`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `proto/` | 控制协议 schema 与 protoc 版本锁 |
| `src/core/` | Go 模块：`internal/` 是控制分发器、帧编解码等共享代码；`service/` 是 Windows 服务入口；`apple/` 编成 macOS 扩展用的 C archive |
| `src/mihomo_desktop/` | Python UI：宿主客户端、models、viewmodels、QML |
| `apple/` | macOS 管理库与系统扩展（待建 Xcode 工程） |
| `packaging/` | Nuitka 入口与打包脚本 |
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

运行测试：

```bash
uv run pytest
```

```bash
go -C src/core test ./...
```

在 Mac 上联调服务：先以开发模式启动服务（监听本地 socket），再用脚本发一次 `GetInfo`。

```bash
go -C src/core run ./service -socket /tmp/mihomo-desktop-dev.sock
```

```bash
uv run python scripts/control_ping.py --socket /tmp/mihomo-desktop-dev.sock
```

启动界面：

```bash
uv run mihomo-desktop
```
