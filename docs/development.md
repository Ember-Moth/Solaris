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

  Windows 11 ARM64 上使用原生 ARM64 Python 构建，产物位于 `build/windows-arm64/`：

  ```powershell
  $env:UV_PYTHON_ARCH = 'aarch64'
  uv sync --frozen --python cpython-3.14.7-windows-aarch64
  uv run --frozen --with nuitka==4.2.2 python scripts/build_windows.py --arch arm64
  ```

  GUI 的 Python、PySide6 和原生扩展必须同架构；构建器会检查解释器及全部打包 PE 文件。Go 服务可交叉编译，GUI 使用
  对应架构的 Windows 构建环境。构建步骤见 [architecture.md](architecture.md) §10。

两个脚本都会先下载钉住版本的 GEO 数据（`scripts/fetch_geodata.py`）。发布用的安装包由 GitHub Actions 的发布工作流
构建（[release.md](release.md)）。

内核通过 go.mod replace 固定到 `Ember-Moth/mihomo` 的 Meta 提交（D38），包含 Naive、AnyTLS+REALITY 和 Snell v4–v6。
用户配置与完整示例见 [mihomo 增强版配置指南](mihomo-fork.md)。
构建脚本启用 `with_naive`，并取 Go 模块图锁定的 Cronet 原生版本：Windows 携带 `libcronet.dll`，macOS 静态链接。
绑定与原生库必须一起升级；不要仅替换 DLL。普通无标签的 Go 测试/开发服务不含 Naive，可在 Mac 上这样验证：

```bash
go -C src/core test -race -tags with_gvisor,with_naive -run '^TestFork' ./internal/engine
go -C src/core run -tags with_gvisor,with_naive ./service -socket /tmp/solaris-dev.sock
```

Windows x64 / ARM64 原生配置测试使用 `with_gvisor,with_naive,with_purego`，测试进程的 PATH 需要包含同架构、锁定版本的 Cronet DLL
目录；已安装的服务只加载自身目录里的 DLL。各目标的构建与验证均已接入 CI。
Go 1.27 的竞态运行时不支持 Windows ARM64，该平台运行普通测试与原生配置测试；竞态检查继续在其他受支持平台运行。

与 fork 联调时，可以用本地 `go.work` 将 `github.com/metacubex/mihomo` 替换为 fork 工作区；该文件不提交。
集成时先发布 fork，再更新 `src/core/go.mod` 的固定版本、go.sum 和许可证清单。使用 `GOWORK=off` 完成全部检查，
确保发布构建只依赖锁定的远端代码。共享 resolver 使用 `Load` / `Store`，hosts 树构建完成后发布，不修改已发布的树。

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

CI 按改动范围选择任务，规则集中在 `scripts/ci_changes.py`。main 推送与最近成功的祖先 CI 提交比较，PR 与 merge-base
比较；未知路径、缺少历史或无法查询成功基线时跑全量。纯说明文档只运行变更判断和 `CI result`，`DESIGN.md`、配置示例
及随包资源仍按构建输入检查。Python 改动保留三平台测试、E2E 和 Windows 打包；Go 改动保留对应的内核与宿主验证。
需要完整复测时，在 Actions 中手动运行 CI 或重新运行全部 jobs；配置必需检查时使用始终返回结果的 `CI result`。

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
改动界面文案后更新翻译目录（源文是中文，英文在 `src/solaris/translations/en.ts`）。用以下命令重新生成；本地可加 `--check`
检查是否一致。CI 的主题与翻译一致性由 pytest 检查，协议绑定在相关输入变化时单独检查：

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

用户文档：[使用指南](usage.md)、[mihomo 配置指南](mihomo-fork.md)、[隐私说明](../PRIVACY.md)。

开发与维护资料：

| 文档 | 内容 |
| --- | --- |
| [维护与发布](maintenance.md) | 验收要求、发布步骤、数据迁移与工作约束 |
| [设计决策](decisions.md) | 各模块共用的设计约定 |
| [系统架构](architecture.md) | 进程、权限、配置准入、控制协议与宿主实现 |
| [界面设计](ui.md) | ViewModel、宿主接口、页面行为与设置 |
| [存储设计](storage.md) | 数据库、备份格式和云端提供方 |
| [分流设计](routing.md) | 规则模型、配置拼装与方案格式 |
| [链式出口设计](exits.md) | WARP、VPN Gate 与 Tor 预留设计 |
| [组网设计](mesh.md) | Tailscale、ZeroTier、EasyTier 的接入与生命周期 |
| [发布与更新](release.md) | 安装包、更新校验、诊断和许可证生成 |
| [macOS 原生开发](../apple/README.md) | 管理库、系统扩展、签名、公证与 iCloud |
| [视觉规范](../DESIGN.md) | 视觉 token、交互和文案规则 |

## 内核参数参考

维护配置示例时，按 `src/core/go.mod` 锁定的版本核对参数、互斥条件和服务端要求：

- [mihomo 增强版 Naive 说明](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/docs/naive.md)
- [Naive 参数与校验](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/adapter/outbound/naive_options.go)
- [AnyTLS 参数与模式互斥](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/adapter/outbound/anytls.go)
- [REALITY 参数](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/adapter/outbound/reality.go)与[握手实现](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/component/tls/reality.go)
- [Snell 参数](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/adapter/outbound/snell.go)与[v4–v6 适配](https://github.com/Ember-Moth/mihomo/blob/c2de0f865c7e7723220df3b9033d208afa4e2388/adapter/outbound/snell_sing.go)
- [Solaris 的内核集成与约束](architecture.md)

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
