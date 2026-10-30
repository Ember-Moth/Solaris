# 发布与更新

本文描述 M6 里界面这一侧的四件事：检查更新与下载（决策 D11）、诊断信息导出、第三方许可证清单、订阅一键导入
（决策 D6）。签名与公证的发布工作流见 [apple/README.md](../apple/README.md)（M5），Windows 安装器见
[architecture.md](architecture.md) 第 9 节（M4）。界面放在设置页的「关于」分组（[ui.md](ui.md) 第 8 节）。

状态：四项已实现（2026-10-30，阶段 D 第一轮）。现在还没有任何 Release，检查更新只用本机的假服务器测过；Windows
的签名方案未定，签名者是一个占位常量，Windows 安装包暂不签名；`solaris:` 和 `clash:` 两个 scheme 由打包向系统
注册（macOS 的 Info.plist，Windows 安装器写在 HKLM，第 6 节）；许可证清单还缺的几项见第 5 节。

## 1. 发布物

每个版本是 GitHub 仓库 `Ember-Moth/Solaris` 的一个 Release，由发布工作流创建。界面只认下面这套约定，发布工作流
必须照此命名：

| 项目 | 约定 |
| --- | --- |
| 标签 | `v<版本>`，版本即 `pyproject.toml` 的 `version`，形如 `0.2.0`（三段数字）。预发布和草稿不提供给更新 |
| macOS 安装包 | `Solaris-<版本>-macos-arm64.dmg`：内含 `Solaris.app`；磁盘映像用 Developer ID（Team `GY9ZKZMAZA`）签名、公证并 staple |
| Windows 安装包 | `Solaris-<版本>-windows-x64-setup.exe`：NSIS 安装器（architecture.md 第 9 节），Authenticode 签名。签名方案定下之前发布工作流产出的安装包不签名，更新时会在第 3 节第 4 步被拒 |
| 校验和 | `SHA256SUMS`：`sha256sum` 的输出格式，每行 `<64 位小写十六进制>  <文件名>`，覆盖本次发布的每个安装包 |
| 源代码 | `Solaris-<版本>-source.tar.gz`：本仓库在这个标签的代码，加上 `go mod vendor` 得到的全部 Go 依赖（第 4 节） |

只做 Windows x64 和 macOS arm64（D2）。其他平台、其他架构的界面不提供下载，「检查更新」照常显示有没有新版本。

## 2. 检查更新

代码在 `src/solaris/update/`：`releases.py` 读 GitHub、`download.py` 下载与校验和、`signature.py` 校验签名，
`viewmodels/about.py` 是设置页「关于」分组的 ViewModel。

- **来源**：`GET https://api.github.com/repos/Ember-Moth/Solaris/releases/latest`，不需要认证。请求头
  `Accept: application/vnd.github+json`、`X-GitHub-Api-Version: 2022-11-28`、`User-Agent: solaris/<版本>`。
  这个接口只返回最新的正式发布，跳过草稿和预发布；仓库还没有任何发布时它回 404，界面当作已是最新版本。
  未认证的请求每个 IP 每小时 60 次，自动检查一天最多几次，够用；超限时 GitHub 回 403 或 429，界面显示
  「检查更新失败：HTTP 403」。
- **版本比较**：标签去掉开头的 `v` 后必须是 `X.Y.Z`，按三段整数比较；当前版本是 `solaris.__version__`（取自
  安装的包元数据，打包时须保留，见第 6 节）。标签不是这个形式时视为检查失败（「发布的版本号无法识别」），
  不会把它当成新版本。
- **下载地址只接受 https**，测试用的回环地址（`127.0.0.1`、`localhost`）例外。
- **自动检查**（设置项见 ui.md 第 8 节）：`update_check` 打开时，界面启动 30 秒后检查一次，之后每隔
  `update_check_hours`（每天、每 3 天、每周）检查一次。上次检查的时间不存储：界面一般连续运行很多天，每次启动多查
  一次无妨。设置改动立即重新排定下一次检查。手动检查不受这两项影响。
- **结果**写在「版本」一行的说明里：「已是最新版本 · 刚刚检查」、「有新版本 0.2.0」（右侧按钮变成「下载」，旁边
  「发布说明」链接打开 Release 页面）、「检查更新失败：…」。有新版本时只在设置页提示，不弹窗，也不打扰其他页面。

## 3. 下载、校验与安装

点「下载」后依次进行，每一步失败都停在那一步，删掉已下载的文件，原因写在说明里：

1. **选安装包**：按平台和架构从 Release 的资源里找第 1 节命名的文件，找不到就是「这个版本没有适合本机的安装包」。
   资源声明的大小超过 1 GiB 也拒绝。
2. **取校验和**：下载 `SHA256SUMS`（不超过 64 KiB），找到安装包那一行。没有这个文件或没有那一行就拒绝
  （「发布没有附带校验和」），不跳过校验。
3. **下载**到用户数据目录的 `updates/`，先写 `<文件名>.part`，边下载边算 SHA-256，标题行显示进度，可以取消。
   收到的字节超过声明的大小立即停止；完成后大小和哈希都对上才改成正式文件名。开始下载前和界面启动时清空
   `updates/` 里的旧文件（删不掉的，如正在运行的安装器，就留着）。
4. **校验签名**（`update/signature.py`），都在工作线程里运行：
   - **macOS**：对磁盘映像依次运行
     - `codesign --verify --deep --strict --verbose=2`：签名有效、内容未被改动；
     - `codesign --display --verbose=2`：输出里的 `TeamIdentifier` 必须是 `GY9ZKZMAZA`；
     - `spctl --assess --type open --context context:primary-signature --verbose=2`：Gatekeeper 接受，也就是
       公证过的 Developer ID。

     不挂载磁盘映像去单独验证里面的 `.app`：映像的签名封住了整个映像的内容，公证时 Apple 已扫描过里面的
     代码，用户首次打开 App 时 Gatekeeper 还会再检查一次。挂载会在 Finder 里留下一个卷，也多一个失败点。
   - **Windows**：经 ctypes 调用 `WinVerifyTrust`（`WINTRUST_ACTION_GENERIC_VERIFY_V2`，整条证书链检查吊销，
     不弹任何界面），通过后用 `WTHelperProvDataFromStateData` 取出签名者证书，`CertGetNameStringW` 读出它的
     显示名，必须等于 `signature.WINDOWS_SIGNER`。签名方案还没定（roadmap 第三轮 M6），这个常量先是占位值；
     证书定下来以后改成证书的主题名。在那之前任何安装包都会因签名者不符被拒绝，这是有意的。
   - 其他平台没有安装包，不会走到这一步。
5. **确认安装**：校验通过后按钮变成「打开安装包…」。点它先弹普通确认框「打开 Solaris 0.2.0 的安装包？」，
   正文写明接下来发生什么：
   - macOS：「会在访达中打开磁盘映像，把 Solaris 拖到"应用程序"文件夹完成升级。替换前先退出界面，隧道继续运行。」
   - Windows：「安装器会请求管理员权限，并在升级前关闭界面。」

   确认后再算一次哈希（文件在用户可写的目录里），对上了才用系统打开它：macOS 由 Finder 挂载映像，Windows 由
   ShellExecute 运行安装器、弹出 UAC。界面从不静默安装，也不自己替换文件；升级本身由安装器和 Finder 完成
   （architecture.md 第 9 节）。

错误码与说明：

| 码 | 说明 |
| --- | --- |
| `check_failed` | 「检查更新失败：…」，细节是网络错误或 HTTP 状态 |
| `bad_release` | 「发布的版本号无法识别」或 Release 的 JSON 不完整 |
| `no_installer` | 「这个版本没有适合本机的安装包」 |
| `checksum_missing` | 「发布没有附带校验和」 |
| `checksum_mismatch` | 「下载的文件与校验和不符」 |
| `download_failed` | 「下载失败：…」 |
| `signature_invalid` | 「安装包的签名无效：…」，细节是 codesign、spctl 或 WinVerifyTrust 的原话 |
| `signer_mismatch` | 「安装包不是由 Solaris 的开发者签名的」 |

## 4. 诊断信息

「关于」分组里的「诊断信息」一行，按「导出…」选择保存位置，默认文件名 `solaris-diagnostics-<日期>-<时间>.zip`。
代码在 `src/solaris/diagnostics.py`。zip 里有：

| 文件 | 内容 |
| --- | --- |
| `about.json` | 界面版本、Python、PySide6 与 Qt 的版本、操作系统与架构、是否打包版、界面语言、导出时间 |
| `host.json` | 宿主的 `GetInfo` 和 `GetStatus`；连不上服务时写错误码和原话 |
| `settings.json` | 设置（第 8 节的表），以及脱敏后的订阅、覆写和分流概况，见下文 |
| `mihomo.log` | 日志页缓冲里的全部内核日志（最多 5000 条），格式与日志页的导出相同 |

**脱敏**按白名单进行，不认识的内容一律不写：

- 设置项逐个列在 `diagnostics.SETTINGS_EXPORTED`（照写）或 `SETTINGS_WITHHELD`（只写"已设置"与否）里，测试要求
  `Settings` 的每个字段都在其中之一，所以新增设置项时必须决定它算哪一类。现有设置项都不含机密，全部照写。
- 订阅只写序号、类型、是否当前配置、是否加入节点池、自动更新间隔、节点数、有没有用量信息、更新时间和更新是否失败；
  不写名称、订阅链接、文件路径和原文。
- 覆写只写有没有全局覆写、哪几份订阅有自己的覆写（按序号），不写原文。
- 分流写方式（订阅自带 / 我的方案）、方案数、正在使用的方案里策略组和规则的条数；不写规则内容和信息节点表达式。
- 令牌（Google 备份、WARP 账户）存在系统凭据库里，诊断不读凭据库。

内核日志里有访问过的域名和节点名，所以「诊断信息」一行的说明写明「含内核日志，其中有访问过的网站」，由用户
决定是否分享。界面自己的 Python 日志和崩溃日志还没有收集，留到 M6 收尾。

## 5. 第三方许可证

`scripts/gen_licenses.py` 生成两份文件，提交进仓库（与 Theme.qml、`.qm` 一样），打包时随包附带：

- `src/solaris/licenses/third_party.json`：组件清单，每项是名称、版本、SPDX 许可证表达式、类别、主页，以及它的
  各段全文在下一个文件里的编号。设置页的「开源许可」对话框读它；
- `src/solaris/licenses/THIRD_PARTY_NOTICES.txt`：开头是 Solaris 自己的许可声明和源代码的获取方式，然后是每个
  组件的版权与许可证全文。完全相同的全文只写一次，列出共用它的组件。

覆盖的组件：

| 类别 | 来源 |
| --- | --- |
| Python | 从 `solaris` 的 `Requires-Dist` 递归求出的运行时依赖（protobuf、PySide6 及其拆分出的包、shiboken6），读安装的包元数据和 `.dist-info` 里的许可证文件；再加上 CPython 自身（Nuitka 把解释器打进包里），全文取自解释器目录的 `LICENSE.txt` |
| Go | `go list -deps` 列出 Windows 服务（`./service`，`GOOS=windows`）和 macOS 扩展的 Go 库（`./apple`，`GOOS=darwin`）实际链接的模块，标签 `with_gvisor`，含 mihomo；许可证文件取自模块缓存里模块的根目录，根目录没有时取被链接的包所在目录。另加 Go 标准库（`$GOROOT/LICENSE`） |
| Qt | Qt 6.12.0 本身（LGPL-3.0-only，全文在 `package/licenses/LGPL-3.0-only.txt`），以及随附的 Qt 模块（Core、GUI、Network、Qml、Quick、Quick Controls、Image Formats）里的第三方代码。后者来自 Qt 文档的《Third-Party Code Used in Qt》和各组件的说明页，由 `--qt-docs <Qt 文档目录>` 读取后存成 `package/licenses/qt.json`，平时生成时直接用它；升级 Qt 时用新版本的文档重新生成 |
| 字体 | `ui/fonts/` 下的 Inter 和 JetBrains Mono，版本读自字体文件的 name 表，全文是各自的 `OFL.txt` |

- **识别许可证**：先用包元数据里的 SPDX 表达式（`License-Expression`，其次 `License`）；Go 模块和没有元数据的
  组件先看全文开头的标题（GPL、LGPL、Apache、MPL、OFL 等；标题前的声明允许"任何更新的版本"时记为
  `-or-later`），再按特征句识别 MIT、BSD、ISC、Unlicense、Zlib。先看标题，是因为许可证会互相引用：GPL 的
  全文提到 LGPL，OFL 的开头与 MIT 相同。识别不出来时脚本报错，要在脚本的 `LICENSE_OVERRIDES` 里为这个模块
  写明，而不是写"未知"。Qt 文档里的许可证名称按 `QT_LICENSE_NAMES` 换成 SPDX。
- **现有的特例**：`github.com/metacubex/chacha` 的许可证文件在子目录里（MIT）；`github.com/rasky/go-lzo`
  （mihomo 的 OpenVPN 传输引入）只附 GPL-2.0 全文，它说明自己沿用 LZO 的许可证，而 LZO 是"GPL 第 2 版或任何
  更新的版本"，所以记为 `GPL-2.0-or-later`，可以与 GPL-3.0 的代码合并发布。本机没有 Qt Image Formats 的文档，
  它的 libtiff 和 libwebp 两项只写了名称、许可证和 Qt 在线文档的地址，没有全文；界面不用 TIFF 和 WebP，打包时
  可以不带这两个插件。CPython 自带的第三方库（OpenSSL、SQLite、libffi 等）还没有逐项列出，只附了 CPython 的
  `LICENSE.txt`。这几项在首发前补齐（M6 收尾）。
- **`--check`** 重新生成并与提交的文件比较，需要 Go 和下载好的模块缓存。`tests/test_licenses.py` 不需要 Go：
  它核对清单里 Python 组件的版本与当前环境一致、Go 模块的版本与 `go.mod` 一致、每项都有全文，所以升级依赖后
  忘了重新生成会失败。
- **源代码**：GPL-3.0 要求随二进制提供对应的源代码。每个 Release 附带第 1 节的源代码包（含 vendor 的 Go 依赖，
  也就包括 mihomo）；Qt 和 PySide6 的源代码在 download.qt.io 的同一版本下。NOTICES 的开头写明这两处。
- **设置页**：「关于」分组的「开源许可」一行写组件的个数，「查看…」打开对话框：搜索框加上按类别分节的列表
  （名称、版本、许可证），样式与可搜索列表相同，只是没有复选框；「打开全文」用系统的文本查看器打开 NOTICES
  文件。

## 6. 打包须知

- Nuitka 打包时，`package/entry.py` 的 `nuitka-project` 选项把 `src/solaris/licenses/` 复制到
  `solaris/licenses/`，把仓库根目录的 `LICENSE` 复制到程序目录；还要带上 `solaris` 的包元数据
  （`--include-distribution-metadata=solaris`），否则 `__version__` 是 `0.0.0`，每个 Release 都会被当成新版本。
- Windows 的界面由 `scripts/build_windows.py` 用同一个 `package/entry.py` 构建（architecture.md 第 10 节），这些选项
  两个平台共用。
- `solaris:` 和 `clash:` 两个 scheme 由打包注册：macOS 在 `Info.plist` 的 `CFBundleURLTypes` 里（M5），Windows
  由安装器写在 HKLM 的 `Software\Classes\solaris` 和 `Software\Classes\clash` 下，命令是
  `"<安装目录>\Solaris.exe" "%1"`。写在 HKLM 而不是 HKCU：安装器以管理员身份运行，这个管理员不一定是使用者本人。
  `clash:` 可能已被其他客户端注册：安装时覆盖 HKLM 里的（用户自己 HKCU 里的仍优先），卸载时只删除仍指向本程序的键。

## 7. 一键导入

机场网站上的「一键导入」按钮打开 `solaris://install-config?url=<订阅链接>&name=<名称>`，或 Clash 系客户端通用的
`clash://install-config?url=…&name=…`（D6）。代码在 `src/solaris/desktop/links.py`。

- **解析**：scheme 是 `solaris` 或 `clash`（不区分大小写），主机是 `install-config`（允许末尾的 `/`）；查询参数按
  `application/x-www-form-urlencoded` 解码，`url` 必填，`name` 可选（去掉首尾空白，最长 100 个字符）。`url` 必须是
  http 或 https 的链接。不符合的链接视为无法识别。没有编码的 `&` 会把订阅链接截断，这种链接无法正确识别，与其他
  客户端相同。
- **到达**：
  - macOS：LaunchServices 发来 Apple Event，Qt 把它转成应用对象上的 `QFileOpenEvent`（`url()` 是这个链接）。
    `links.LinkReceiver` 是装在应用对象上的事件过滤器，从 `app.main` 一开始就装好；冷启动时链接可能在界面加载
    之前到达，先存着，界面加载后再交出。
  - Windows：安装器注册的命令把链接作为命令行参数传入，`app.main` 从 `sys.argv` 里取第一个 `solaris:` 或
    `clash:` 开头的参数。
  - 已经有界面在运行时，后来的界面把链接转发给它（ui.md 第 8 节"单实例"），然后退出。
- **处理**（`ViewModels.open_link`）：显示并激活窗口，切到订阅页，打开"从链接导入"的表单，填好订阅链接和名称，
  焦点放在订阅链接上，由用户检查后按「导入」。之后走正常的导入流程：下载、`inspect_source`、校验，失败照常写在
  表单里。正在导入时到达的链接被忽略。无法识别的链接也显示窗口和订阅页，标题栏写「无法识别的导入链接」。
- 链接只预填表单，不会自动导入：任何网页都能打开这个链接，必须由用户确认。

## 8. 测试

- `tests/test_update.py`：本机起一个假的 GitHub（`tests/support/releases.py`，`http.server`，回环地址），覆盖有新
  版本、已是最新、还没有发布、标签无法识别、HTTP 错误、没有本机安装包、没有校验和、哈希不符、大小与声明不符、
  取消下载、签名被拒、签名者不符、打开前文件被换；签名校验用替身。macOS 上另用系统自带的 App 真实运行
  `codesign`，确认 Team ID 不符时被拒；Windows 上真实调用 `WinVerifyTrust`，未签名的文件被拒，找得到带内嵌签名的
  程序（如 PowerShell 7）时确认签名者不符被拒。
- `tests/test_about.py`：「关于」分组的各个状态与按钮、自动检查的计时与设置项、确认后才打开安装包；诊断 zip
  的内容，订阅链接、名称、路径、覆写原文和方案内容不出现在任何文件里，设置项全部归了类；许可证列表与搜索。
- `tests/test_licenses.py`：见第 5 节。
- `tests/test_links.py`：两种 scheme 的解析和拒绝；第二个界面把链接转交给第一个；`QFileOpenEvent`；打开订阅页、
  预填表单、焦点在订阅链接上、没有自动导入。
- 组装整个界面的测试和预览都不连 GitHub：测试用 `offline_updater`，`scripts/preview.py` 用回环地址上的假发布，
  `--update` 时它比当前版本新。
