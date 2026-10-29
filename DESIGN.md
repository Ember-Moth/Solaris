---
version: alpha
name: Mihomo Desktop
description: 石墨灰底、白色分组、单一信号蓝强调的原生风格桌面代理客户端；信息密集、控件紧凑，拒绝"AI 仪表盘"式的大按钮和数字卡片。
colors:
  primary: "#1F5EFF"
  primary-hover: "#1B53E0"
  primary-pressed: "#1749CC"
  on-primary: "#FFFFFF"
  link: "#1846C7"
  link-hover: "#123A9E"
  background: "#F3F4F6"
  sidebar: "#E9EBEF"
  sidebar-hover: "#E0E3E8"
  surface: "#FFFFFF"
  surface-subtle: "#F7F8FA"
  surface-selected: "#ECEEF1"
  control-track: "#E3E5E9"
  divider: "#ECEEF1"
  border: "#DCDFE4"
  border-button: "#C9CDD4"
  border-control: "#828893"
  text: "#15171C"
  text-secondary: "#3D4350"
  text-tertiary: "#4B5260"
  text-muted: "#5F6673"
  success: "#0A6B5B"
  warning: "#8A5300"
  latency-poor: "#9A3412"
  danger: "#B42318"
  info: "#1846C7"
  chart-upload: "#C2410C"
  scrim: "#15171C33"
  shadow-overlay: "#15171C1F"
  shadow-segment: "#15171C1F"
  shadow-nav: "#15171C14"
  dark:
    primary: "#6495FF"
    primary-hover: "#76A1FF"
    primary-pressed: "#88AEFF"
    on-primary: "#101216"
    link: "#8AAEFF"
    link-hover: "#B5CBFF"
    background: "#1B1D20"
    sidebar: "#202227"
    sidebar-hover: "#26292E"
    surface: "#2B2D33"
    surface-subtle: "#30343A"
    surface-selected: "#383B43"
    control-track: "#121416"
    divider: "#34373E"
    border: "#3B3F46"
    border-button: "#4C505A"
    border-control: "#818691"
    text: "#E6E9ED"
    text-secondary: "#CBCFD6"
    text-tertiary: "#B7BCC4"
    text-muted: "#A1A6AF"
    success: "#45C4A6"
    warning: "#E5A443"
    latency-poor: "#F38B5E"
    danger: "#FF8077"
    info: "#8AAEFF"
    chart-upload: "#F28A4B"
    scrim: "#00000080"
    shadow-overlay: "#00000066"
    shadow-segment: "#00000052"
    shadow-nav: "#0000003D"
typography:
  title:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: 600
    lineHeight: 1.4
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
  body-md-strong:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.5
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.2
  label-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 500
    lineHeight: 1.2
  label-sm-strong:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: 600
    lineHeight: 1.2
  caption:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
  section-label:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: 600
    lineHeight: 1.4
  data-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.5
  data-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.6
rounded:
  xs: 4px
  sm: 6px
  md: 8px
  full: 9999px
spacing:
  base: 4px
  1: 4px
  2: 8px
  3: 12px
  4: 16px
  5: 20px
  6: 24px
  8: 32px
  10: 40px
  page-x: 32px
  page-top: 20px
  page-bottom: 40px
  sidebar-width: 216px
  group-column-min: 400px
  window-default-width: 1280px
  window-default-height: 800px
  window-min-width: 960px
  window-min-height: 640px
  control-sm: 32px
  control-md: 36px
  row-single: 44px
  row-double: 52px
  row-header: 32px
  overlay-gap: 4px
  overlay-margin: 8px
  overlay-shadow-y: 4px
  overlay-shadow-blur: 16px
  menu-min-width: 180px
  popover-min-width: 240px
  popover-max-width: 360px
  dialog-width: 400px
  dialog-width-wide: 640px
  editor-min-height: 240px
opacity:
  disabled: 0.4
components:
  page:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text}"
    typography: "{typography.body-md}"
  sidebar:
    backgroundColor: "{colors.sidebar}"
    textColor: "{colors.text-secondary}"
    width: "{spacing.sidebar-width}"
  nav-item:
    backgroundColor: "{colors.sidebar}"
    textColor: "{colors.text-secondary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.md}"
    height: "{spacing.row-single}"
    padding: "{spacing.3}"
  nav-item-hover:
    backgroundColor: "{colors.sidebar-hover}"
    textColor: "{colors.text}"
  nav-item-active:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-md-strong}"
  page-header:
    textColor: "{colors.text}"
    typography: "{typography.title}"
  header-rule:
    backgroundColor: "{colors.border}"
    height: 1px
  section-label:
    textColor: "{colors.text-muted}"
    typography: "{typography.section-label}"
  group:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
  row-single:
    height: "{spacing.row-single}"
    padding: "{spacing.4}"
  row-double:
    height: "{spacing.row-double}"
    padding: "{spacing.4}"
  row-hover:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.text}"
  row-selected:
    backgroundColor: "{colors.surface-selected}"
    textColor: "{colors.text}"
    typography: "{typography.body-md-strong}"
  row-secondary-text:
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
  divider:
    backgroundColor: "{colors.divider}"
    height: 1px
  table-header:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
    height: "{spacing.row-header}"
    padding: "{spacing.4}"
  value-mono:
    textColor: "{colors.text-secondary}"
    typography: "{typography.data-md}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-md}"
    padding: 14px
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.on-primary}"
  button-primary-pressed:
    backgroundColor: "{colors.primary-pressed}"
    textColor: "{colors.on-primary}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-md}"
    padding: 14px
  button-secondary-hover:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.text}"
  button-secondary-pressed:
    backgroundColor: "{colors.surface-selected}"
    textColor: "{colors.text}"
  button-compact:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.label-sm}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-sm}"
    padding: "{spacing.3}"
  icon-button:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.sm}"
    size: 28px
  link:
    textColor: "{colors.link}"
    typography: "{typography.body-sm}"
  link-hover:
    textColor: "{colors.link-hover}"
  segmented-track:
    backgroundColor: "{colors.control-track}"
    rounded: "{rounded.sm}"
    padding: 2px
  segmented-item:
    backgroundColor: "{colors.control-track}"
    textColor: "{colors.text-tertiary}"
    typography: "{typography.label-sm}"
    rounded: "{rounded.xs}"
    height: "{spacing.control-sm}"
    padding: "{spacing.3}"
  segmented-item-selected:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.label-sm-strong}"
  switch-on:
    backgroundColor: "{colors.primary}"
    rounded: "{rounded.full}"
    width: 40px
    height: 24px
  switch-off:
    backgroundColor: "{colors.border-control}"
    rounded: "{rounded.full}"
    width: 40px
    height: 24px
  switch-thumb:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.full}"
    size: 18px
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-md}"
    padding: 10px
  input-compact:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-sm}"
    padding: "{spacing.2}"
  status-dot-running:
    backgroundColor: "{colors.success}"
    rounded: "{rounded.full}"
    size: 8px
  status-dot-stopped:
    backgroundColor: "{colors.text-muted}"
    rounded: "{rounded.full}"
    size: 8px
  latency-good:
    textColor: "{colors.success}"
    typography: "{typography.data-sm}"
  latency-fair:
    textColor: "{colors.warning}"
    typography: "{typography.data-sm}"
  latency-poor:
    textColor: "{colors.latency-poor}"
    typography: "{typography.data-sm}"
  latency-timeout:
    textColor: "{colors.text-muted}"
    typography: "{typography.data-sm}"
  rate-download-glyph:
    textColor: "{colors.primary}"
    typography: "{typography.data-md}"
  rate-upload-glyph:
    textColor: "{colors.chart-upload}"
    typography: "{typography.data-md}"
  progress-track:
    backgroundColor: "{colors.divider}"
    rounded: "{rounded.full}"
    height: 4px
  progress-fill:
    backgroundColor: "{colors.text-tertiary}"
    rounded: "{rounded.full}"
    height: 4px
  notice-warning:
    textColor: "{colors.warning}"
    typography: "{typography.caption}"
  log-line:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.data-sm}"
  log-level-debug:
    textColor: "{colors.text-muted}"
    typography: "{typography.data-sm}"
  log-level-info:
    textColor: "{colors.info}"
    typography: "{typography.data-sm}"
  log-level-warn:
    textColor: "{colors.warning}"
    typography: "{typography.data-sm}"
  log-level-error:
    textColor: "{colors.danger}"
    typography: "{typography.data-sm}"
  button-destructive:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.danger}"
    typography: "{typography.label-md}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-md}"
    padding: 14px
  button-destructive-hover:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.danger}"
  button-destructive-pressed:
    backgroundColor: "{colors.surface-selected}"
    textColor: "{colors.danger}"
  input-error:
    textColor: "{colors.danger}"
    typography: "{typography.caption}"
  code-editor:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.data-md}"
    rounded: "{rounded.sm}"
    height: "{spacing.editor-min-height}"
    padding: 10px
  overlay-shadow:
    backgroundColor: "{colors.shadow-overlay}"
  scrim:
    backgroundColor: "{colors.scrim}"
  menu:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    width: "{spacing.menu-min-width}"
    padding: "{spacing.1}"
  menu-item:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.xs}"
    height: "{spacing.control-sm}"
    padding: "{spacing.2}"
  menu-item-highlighted:
    backgroundColor: "{colors.surface-selected}"
    textColor: "{colors.text}"
  menu-item-destructive:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.danger}"
  menu-item-destructive-highlighted:
    backgroundColor: "{colors.surface-selected}"
    textColor: "{colors.danger}"
  menu-check:
    textColor: "{colors.primary}"
    size: 14px
  menu-separator:
    backgroundColor: "{colors.divider}"
    height: 1px
  popover:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.md}"
    padding: "{spacing.3}"
  dialog:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    width: "{spacing.dialog-width}"
    padding: "{spacing.5}"
  dialog-wide:
    width: "{spacing.dialog-width-wide}"
  dialog-title:
    textColor: "{colors.text}"
    typography: "{typography.body-md-strong}"
  dialog-body:
    textColor: "{colors.text-secondary}"
    typography: "{typography.body-sm}"
---

# Mihomo Desktop

## Overview

Mihomo Desktop 是 Windows 与 macOS 上的 mihomo 图形客户端，界面用 PySide6 / QML 实现。用户主要做四件事：确认隧道是否在运行、切换出站模式和节点、管理订阅、排查连接和日志。界面要让这四件事一眼可见、一两下可改，其余时间保持安静。

视觉主题是**原生系统工具**：像 macOS「系统设置」或 Windows 11「设置」，而不是营销页或数据大屏。浅灰石墨底，内容放在白色分组里，组内用细线分行；全局只有一个信号蓝强调色，只用在开关开启、主操作、焦点和当前项标记上。信息密度偏高，控件紧凑，数字用等宽字体。

这份方向来自 Claude Design 原型的第二版（[画布](https://claude.ai/artifact/DVBcxqL7s2aX96F9enjwfj)）。第一版因为"AI 味太重"被否决，被删掉的做法列在 [Do's and Don'ts](#dos-and-donts) 里，是这份契约最重要的约束。

- **平台**：Qt Quick。文中 `px` 指 Qt 的设备无关像素，高分屏由 Qt 缩放。
- **窗口**：使用系统原生标题栏，不做无边框自绘窗口。
- **语言**：界面提供简体中文和英文。简体中文是源语言，英文是它的译文；默认跟随系统，系统首选语言是中文时用中文，其他一律用英文。内核日志、协议名、规则，以及配置里的组名和节点名保持原文，不翻译。英文的写法见 [英文文案](#英文文案)。
- **外观**：浅色和深色两套。默认跟随系统，系统在运行中切换外观时界面立即跟着变；也可以在设置里固定为浅色或深色。两套外观共用同一组语义角色和这份契约的全部规则，只换颜色取值（见 [深色](#深色)）；字体、间距、圆角和动效不变。原生标题栏随外观切换。

## Colors

颜色 token 按语义角色命名。frontmatter 里 `colors` 下直接列出的是浅色取值；`colors.dark` 为每个角色给出深色取值，键名一一对应。组件引用的 `{colors.X}` 在深色外观下取 `{colors.dark.X}`。增删角色时两组同时改，`scripts/gen_theme.py` 发现两组的键不一致就报错。下面先写浅色，深色见 [深色](#深色)。其他章节说的「白底」「白色分组」指 `surface`，「浅灰底」指 `surface-selected`，深色外观下取各自的深色值。

色彩分三层：窗口底色 `background` `#F3F4F6`，侧栏 `sidebar` `#E9EBEF`，内容容器 `surface` `#FFFFFF`。层级靠底色差和 1px 线条表达，不靠阴影。

- **强调色** `primary` `#1F5EFF`：开关开启、主按钮、焦点环、当前导航项图标、下载速率的线与箭头。悬停 `primary-hover` `#1B53E0`，按下 `primary-pressed` `#1749CC`；三者上的白字对比度分别为 5.1、6.3、7.3。每个页面、每个对话框最多一个主色按钮。
- **链接** `link` `#1846C7`，悬停 `link-hover` `#123A9E`。只用于跳转到其他页面的文字链接（如「管理」「全部 37 个」）。
- **文字**四级：`text` `#15171C` 正文与标题；`text-secondary` `#3D4350` 侧栏导航、等宽数值；`text-tertiary` `#4B5260` 未选中的分段项、次要数值；`text-muted` `#5F6673` 分组标题、说明文字、表头。`text-muted` 在最深的侧栏底色上仍有 4.8:1。
- **选中与悬停**：`surface-subtle` `#F7F8FA` 用于行悬停和表头；`surface-selected` `#ECEEF1` 用于选中行；`sidebar-hover` `#E0E3E8` 用于导航项悬停；`control-track` `#E3E5E9` 是分段控件的底槽。
- **线条**四种，按用途选，不混用：
  - `divider` `#ECEEF1`：组内行分隔、进度条底槽；
  - `border` `#DCDFE4`：分组外框、页面标题下方的分隔线、侧栏右边线；
  - `border-button` `#C9CDD4`：带文字的按钮外框，文字本身已经标明这是按钮，所以不要求 3:1；
  - `border-control` `#828893`：输入框、下拉框、单选圈和关闭状态的开关轨道。这些控件靠边框识别，在白底上 3.6:1、窗口底色上 3.2:1，满足非文字 3:1。
- **状态色只用于文字和小圆点**，不铺底色：
  - `success` `#0A6B5B`：服务运行中、延迟小于 150 ms；
  - `warning` `#8A5300`：延迟 150–300 ms、WARN 日志、需要留意的提示（如开启局域网连接）；
  - `latency-poor` `#9A3412`：延迟 300 ms 以上；
  - `danger` `#B42318`：ERROR 日志、破坏性操作的文字；
  - `info` `#1846C7`：INFO 日志级别。

  这些颜色在白底上都在 6:1 以上。延迟和日志级别始终同时显示数字或文字，颜色只是辅助。
- **图表**：下载用 `primary`，上传用 `chart-upload` `#C2410C`。蓝与橙在明度和色相上都能区分，不用红绿对比。
- **半透明色**只有四种，都以 `#RRGGBBAA` 书写（`scripts/gen_theme.py` 生成 Theme.qml 时换成 Qt 的 `#AARRGGBB` 顺序），浅色下都是文字色加不透明度：
  - `scrim` `#15171C33`：20%，只铺在模态对话框背后；
  - `shadow-overlay` `#15171C1F`：12%，只用于浮层阴影；
  - `shadow-segment` `#15171C1F`、`shadow-nav` `#15171C14`：12% 和 8%，分别是分段控件选中项和当前导航项的凸起阴影（见 [Elevation & Depth](#elevation--depth)）。

### 深色

参照 macOS 与 Windows 11 的原生深色外观：不用纯黑，层级靠明度差表达，越靠上的层越亮；仍然只有一个强调色。深色值只取 `colors.dark`，不要在实现里从浅色值反相、调暗或加透明度临时推导。以下括号里的数字是 CIE L\* 明度。

- **底色分层**：窗口底色 `background` `#1B1D20`（11）→ 侧栏 `sidebar` `#202227`（13）→ 分组、浮层和输入框 `surface` `#2B2D33`（19）。行悬停和表头 `surface-subtle` `#30343A`（22），选中行 `surface-selected` `#383B43`（25），导航项悬停 `sidebar-hover` `#26292E`（17）。与浅色不同，侧栏比窗口底色亮一级，和 macOS 深色侧栏一致；当前导航项仍用 `surface`，比侧栏亮。
- **分段控件底槽** `control-track` `#121416`（6）是唯一比窗口底色暗的颜色：底槽在深色下凹进去，选中项用 `surface`，与底槽差 12 个明度。
- **文字**四级：`text` `#E6E9ED`（不用纯白，大面积暗底上不刺眼）、`text-secondary` `#CBCFD6`、`text-tertiary` `#B7BCC4`、`text-muted` `#A1A6AF`。深色下最亮的底色是 `surface-selected`，`text-muted` 在它上面仍有 4.6:1，在 `surface` 上 5.6:1。
- **强调色** `primary` `#6495FF`，悬停 `primary-hover` `#76A1FF`，按下 `primary-pressed` `#88AEFF`：深色下越操作越亮，与浅色下越操作越暗一样，都是拉开与底色的差距。深色的主色是浅蓝，所以主按钮上的文字 `on-primary` 改为近黑 `#101216`（Windows 11 深色主按钮的做法），三种状态上的对比度为 6.5、7.4、8.5。`primary` 用作线条、图标和焦点环时，在窗口底色上 5.9:1、`surface` 上 4.8:1、`surface-selected` 上 3.9:1；下载速率的箭头是文字，只出现在 `surface` 上。
- **链接**与 **INFO** 级别 `link` / `info` `#8AAEFF`，悬停 `link-hover` `#B5CBFF`，同样是悬停变亮。
- **线条**都比 `surface` 亮，深色下靠亮线勾边：`divider` `#34373E`、`border` `#3B3F46`、`border-button` `#4C505A`。`border-control` `#818691` 在 `surface` 上 3.8:1、窗口底色上 4.6:1、`surface-selected` 上 3.1:1，满足非文字 3:1；开关的圆钮（`surface`）在关闭轨道上 3.8:1，在开启轨道（`primary`）上 4.8:1。
- **状态色**保持浅色的色相，提高明度：`success` `#45C4A6`、`warning` `#E5A443`、`latency-poor` `#F38B5E`、`danger` `#FF8077`。它们在所有深色底色上（包括 `surface-selected`）都不低于 4.5:1。
- **图表**：下载 `primary` `#6495FF`，上传 `chart-upload` `#F28A4B`（`surface` 上 5.6:1），参考线 `divider`。蓝与橙在深色下同样靠色相和明度区分。
- **半透明色**改为纯黑加不透明度。暗底上的阴影不明显，所以比浅色更深；浮层的层次主要靠 1px `border` 亮边和更亮的 `surface`：
  - `scrim` `#00000080`：50%；
  - `shadow-overlay` `#00000066`：40%；
  - `shadow-segment` `#00000052`、`shadow-nav` `#0000003D`：32% 和 24%。
- **焦点环**仍是 2px `primary` 外环，对比度见上文「强调色」。
- **Qt 控件的默认调色板**也从这组 token 取值，包括滚动条、文字选区和 Qt 自带的输入框右键菜单，映射见 `docs/architecture.md` §7.6。

## Typography

拉丁字符用 **Inter**，数据用 **JetBrains Mono**，两者都是 OFL 许可，随应用打包（QML `FontLoader`），许可证文件一并分发。中文由系统字体回退：macOS 为 PingFang SC，Windows 为 Microsoft YaHei UI。Inter 不可用时回退到系统界面字体（SF Pro / Segoe UI Variable），JetBrains Mono 回退到 SF Mono / Consolas。

| 角色 | token | 用途 |
| --- | --- | --- |
| 页面标题 | `title` 18/600 | 每页只有一个，位于标题栏左侧 |
| 正文 | `body-md` 14/400 | 行标签、表格主列 |
| 强调正文 | `body-md-strong` 14/600 | 当前导航项、选中行、对话框标题 |
| 小正文 | `body-sm` 13/400 | 输入框文字、链接、次要列、菜单项、对话框正文 |
| 按钮 | `label-md` 14/500 | 标准按钮、导航项 |
| 紧凑按钮 | `label-sm` 13/500，选中时 `label-sm-strong` 13/600 | 行内按钮、分段控件 |
| 说明 | `caption` 12/400 | 行内第二行说明、表头、图表刻度 |
| 分组标题 | `section-label` 12/600 | 分组上方的灰色小标题 |
| 数据 | `data-md` 13 / `data-sm` 12 等宽 | 速率、流量、延迟、端口、版本号、规则、日志、YAML 编辑框 |

规则：

- 行高中文正文不低于 1.5，日志行 1.6；不对中文加字距。
- 会跳动的数字（速率、计时、计数）一律等宽，避免抖动；静态的小数字可以用 Inter，但要开启等宽数字特性 `tnum`。
- 一个界面最多三种字重：400、500、600。不使用 700 以上的粗体，也不用超过 18px 的字号。
- 文案以名词和短语为主，不写解释性副标题。需要说明时放在行内第二行，用 `caption`，不超过一句。

### 英文文案

英文是中文的译文，语气和信息量与中文相同。下文用直引号标出英文示例。

- **大小写**：页面标题、按钮、导航项、分组标题、表头、菜单项、分段选项和对话框标题一律用 sentence case，只有首词和专有名词首字母大写，如 "Recent connections"、"TUN mode"、"Import from URL"。不用 Title Case，分组标题也不全大写。
- **原文名词**：产品、协议和技术名词保持原写法，如 mihomo、TUN、YAML、VLESS、URL。
- **进行中的状态**以单个字符「…」（U+2026）结尾，不写三个句点，如 "Updating…"、"Saving…"。打开对话框继续输入的菜单项同样以「…」结尾（见「菜单」）。
- **引号与撇号**用印刷体：引用名称用弯双引号 “ ”，对应中文的「」，如 "Delete “Sample A”?"；撇号用 ’，如 "Can’t reach the service"。代码、YAML 和文件过滤器里保持直引号。
- **标点**：标签、状态和错误提示不加句号，错误细节写在冒号之后，如 "Download failed: HTTP 404"。对话框正文是完整的句子，带句号。
- **数量**按单复数变化，如 "1 profile"、"3 profiles"，不写 "profile(s)"。
- **日期与时间**：今年的日期写 "Oct 20"，其他年份写 "Dec 31, 2026"。相对时间以小写开头（"just now"、"5 minutes ago"），可以直接接在 "Updated" 后面。数字、单位和速率与中文写法相同，如 "68 ms"、"8.6 MB/s"。
- **宽度**：英文通常比中文长，固定宽度的位置要以英文核对，并在最小窗口宽度下用两种语言各检查一遍。
  - 侧栏宽 `sidebar-width` 216px，底部服务状态块的文字区只有约 163px（`caption`）。一行放不下的内容在固定位置分行，不靠自动换行把短语拆开：英文的版本信息写成两行，第一行是内核版本，第二行是 "Protocol v1"。
  - 按钮、分段选项和菜单项的文字不截断；表格列宽要放得下英文的常见值，如更新列的 "45 minutes ago"、到期列的 "Dec 31, 2026"。

## Layout

窗口默认 `window-default-width` × `window-default-height`（1280 × 800），最小 `window-min-width` × `window-min-height`（960 × 640）。

- **整体结构**：左侧固定宽度 `sidebar-width`（216px）侧栏，右侧内容区。内容区内边距上 `page-top` 20px、左右 `page-x` 32px、下 `page-bottom` 40px。
- **页面标题栏**：一行，左侧是 `title` 加一段 13px 的状态文字（如「已运行 2:14:36」「5 个代理组」），右侧是本页操作。下方一条 `header-rule`，再隔 20px 开始内容。
- **分组**：分组标题与分组间隔 8px；分组之间上下 20px、左右 24px。宽度足够时分组排成两列，每列不窄于 `group-column-min`（400px）；内容区放不下两列时退为一列，不出现横向滚动。
- **行高**：单行行 `row-single` 44px，双行行（主文字加说明）`row-double` 52px，表头 `row-header` 32px。行内左右内边距 16px。
- **控件高度**：标准 `control-md` 36px（标题栏按钮、表单输入），紧凑 `control-sm` 32px（行内按钮、分段控件、设置里的下拉框和数字框）。
- **点击区域**：桌面端以鼠标为主，36 / 32px 的控件可以接受；但整行可点击的列表项高度不小于 44px，开关和单选圈的可点范围要延伸到整行标签。
- **列表与表格**优先于卡片网格。表格放在分组容器里，列宽不够时只在容器内部横向滚动；主列用省略号截断，数值列右对齐。
- **间距**：以 4px 为基数，只用 4、8、12、16、20、24、32、40。
- **浮层位置**：
  - 菜单和弹出层在触发控件下方打开，间隔 `overlay-gap` 4px。「更多」菜单的右边缘与触发按钮对齐；下拉菜单左对齐，宽度不小于下拉框。下方放不下时改到上方。
  - 任何浮层与窗口边缘至少相距 `overlay-margin` 8px，不超出窗口。
  - 菜单宽度不小于 `menu-min-width` 180px，随最长的项变宽；弹出层宽度在 `popover-min-width` 240px 到 `popover-max-width` 360px 之间。
  - 对话框在窗口内水平、垂直居中，宽度 `dialog-width` 400px；含编辑框的对话框用 `dialog-width-wide` 640px。高度随内容，最多为窗口高度减去上下各 40px，超出时只滚动正文，标题和按钮行不动。

## Elevation & Depth

整体是平的。层级来自三件事：底色分层（窗口 → 侧栏 → 白色分组）、1px 线条、选中底色。

- 分组、表格、日志面板**没有阴影**，只有 `border` 外框。
- 页面内只有两处使用阴影，都是为了表现"凸起的选中块"：
  - 分段控件的选中项：`0 1px 1px`，颜色 `shadow-segment`（浅色即 `rgba(21, 23, 28, 0.12)`）；
  - 侧栏的当前导航项：`0 1px 2px`，颜色 `shadow-nav`（浅色即 `rgba(21, 23, 28, 0.08)`）。
- 浮层（菜单、下拉菜单、弹出层、对话框）共用唯一一级浮层阴影 `overlay-shadow`：向下偏移 `overlay-shadow-y` 4px，模糊 `overlay-shadow-blur` 16px，颜色 `shadow-overlay`，浅色即 `0 4px 16px rgba(21, 23, 28, 0.12)`；再加 1px `border` 外框。浮层之间不再分级。托盘弹出窗尚未设计，届时沿用这一级。
- 深色外观下几何不变，只换颜色：阴影用 `colors.dark` 里更深的黑色半透明，浮层的 `border` 外框比 `surface` 亮，在暗底上勾出边缘。
- **层级**从下到上：
  1. 页面内容；
  2. 菜单与弹出层：非模态，同一时间只开一个，打开新的会关掉旧的；
  3. 遮罩与对话框：模态，同一时间只有一个对话框，不在对话框上再叠对话框；
  4. 从对话框里打开的菜单，位于该对话框之上。

  工具提示始终在最上层。

## Shapes

- **圆角**四档：
  - `xs` 4px：分段控件内的选项、菜单项；
  - `sm` 6px：按钮、输入框、下拉框、分段控件底槽、YAML 编辑框；
  - `md` 8px：分组、表格容器、日志面板、导航项、菜单、弹出层、对话框；
  - `full`：开关、状态圆点、单选圈、进度条。

  嵌套时内层圆角等于外层减去内边距，例如分段控件底槽 6px、内边距 2px，选项 4px；菜单 8px、内边距 4px，菜单项 4px。浮层不使用比 `md` 更大的圆角。
- **线宽**一律 1px；单选圈 1.5px。
- **图标**：线性图标，24 网格、2px 描边、圆角端点与拐角（与 Lucide 同一风格）。导航 18px，行内 14–16px。不使用填充图标、插画或 emoji。
- **侧栏左上角的标记**目前是占位图形，等产品命名后替换为正式标识。

## Components

- **侧栏导航**：
  - 导航项默认没有自己的底色（与侧栏同色），悬停 `nav-item-hover`。
  - 当前项使用白底、600 字重，图标改为 `primary`，并带 Elevation 中规定的轻阴影。
  - 侧栏底部用 8px 圆角的浅底块显示服务状态：状态圆点、一行状态文字、内核与协议版本（中文一行，英文分两行，见「英文文案」）。
- **页面标题栏**：见 Layout。连接状态放在概览页标题位置，写作「已连接 / 未连接」。连接与断开是一个普通按钮：已连接时为次要按钮「断开」，未连接时为主按钮「连接」。不做大号圆形开关。
- **分组与行**：
  - 分组标题用 `section-label`；可以在右侧放一个文字链接（如「管理」）。
  - 行的左侧是标签（可加一行 `row-secondary-text` 说明），右侧是值或控件。
  - 整行可点击时，悬停用 `row-hover`，选中用 `row-selected`，并在左侧加一个 `primary` 勾或单选圈。
  - 行的附加状态（如配置「已覆写」）写进第二行说明，用「 · 」与其他信息隔开，不加徽章或彩色标记。
- **按钮**：
  - 主按钮（`button-primary`）每页最多一个，用于该页最主要的创建或启动动作；对话框里的主按钮是它的默认按钮（见下文「对话框」）。
  - 其余按钮都是次要按钮（`button-secondary`），白底加 `border-button` 外框。
  - 行内操作用 `button-compact`；只有图标的操作用 `icon-button`（无边框，与所在行同底色），必须带无障碍名称。
  - 破坏性操作放进「更多」菜单（`menu-item-destructive`），执行前弹出破坏性确认框；不做红色实心按钮。
  - 破坏性确认框里的确认按钮用 `button-destructive`：白底、`border-button` 外框、`danger` 文字，悬停和按下的底色与次要按钮相同。它只出现在确认框里。
- **分段控件**：用于 2–4 个互斥选项（出站模式、排序、日志级别、TCP / UDP 筛选）。选中项用白底加 600 字重，不只靠底色区分。超过 4 个选项改用下拉框。
- **开关**：40 × 24 的轨道，开启用 `switch-on`，关闭用 `switch-off`；圆钮 18px，内缩 3px。开关只用于立即生效的布尔设置，并通过 `aria-checked` 表达状态。
- **输入框与下拉框**：`surface` 底（浅色下为白底）加 `border-control` 外框。设置行里用 `input-compact`，表单里用 `input`。错误状态：外框改为 `danger`，下方用 `input-error`（`caption`、`danger`）原样写出错误，不加图标。选中的文字用 `primary` 底、`on-primary` 字，输入框和 YAML 编辑框相同。
- **YAML 编辑框** `code-editor`：多行等宽文本框，`data-md` 文字、`border-control` 外框、6px 圆角、10px 内边距，最小高度 `editor-min-height` 240px，内容更长时在框内滚动；不自动换行，长行横向滚动。错误状态与输入框相同。
- **延迟**：用等宽文字显示数值，按阈值着色（`latency-good` < 150 ms、`latency-fair` 150–300 ms、`latency-poor` > 300 ms、`latency-timeout` 显示「超时」）。不用彩色药丸。
- **流量**：
  - 当前速率写成 `↓ 8.6 MB/s  ↑ 1.2 MB/s`，箭头用 `rate-download-glyph` / `rate-upload-glyph` 的颜色，数字用 `text`。
  - 趋势图是两条 1.5px 的折线，配浅色水平参考线，没有面积填充，也没有柱状图。
- **用量进度**：4px 高，底槽 `progress-track`，填充 `progress-fill`（中性灰），不用强调色，旁边写出具体数值。
- **表格**：表头 `table-header`；数值列用 `data-sm` 并右对齐；行操作（如关闭连接）是行尾的 `icon-button`。
- **日志**：等宽 `log-line`，三列依次是时间、级别、消息。级别只用文字颜色区分（`log-level-*`），不加底色。暂停状态写在标题栏的状态文字里，不弹出横幅。
- **菜单**（「更多」菜单、下拉菜单）参照 Windows 11 的上下文菜单与 macOS 的菜单：
  - 容器 `menu`：白底、1px `border`、8px 圆角、4px 内边距，带浮层阴影。
  - 菜单项 `menu-item`：32px 高、4px 圆角、`body-sm` 文字，左右内边距 8px，左侧留 14px 的勾选位。可勾选的项勾上时显示 `menu-check`：14px 的 `primary` 勾。
  - 悬停和键盘高亮都用 `menu-item-highlighted`：`surface-selected` 浅灰底，文字不变色。不用强调色铺底。
  - 破坏性的项用 `menu-item-destructive`，高亮时仍是 `danger` 文字（`menu-item-destructive-highlighted`）。它放在菜单最后，前面用 `menu-separator` 隔开：1px `divider`，上下各留 4px。
  - 菜单项只有文字：不加图标、快捷键提示或说明文字，不做多级子菜单。打开对话框继续输入的项以「…」结尾（如「编辑覆写…」）；只弹出确认框的项不加（如「删除」）。
  - 下拉菜单是下拉框的选项列表：当前值带勾；超过 10 项时只显示 10 项，其余在菜单内滚动。
- **弹出层** `popover`：锚定在某个控件旁的少量非模态内容，如一段说明或一两个输入框。容器与菜单相同，内边距 12px，`body-sm` 文字，没有指向触发控件的箭头。点击外部、按 Esc 或再点一次触发控件即关闭，未提交的输入随之丢弃；需要确认或输入较多时改用对话框。
- **对话框** `dialog`：参照 macOS 的 alert 与 Windows 11 的内容对话框。
  - 白底、1px `border`、8px 圆角、20px 内边距，带浮层阴影，背后铺 `scrim`。没有标题栏、关闭按钮、图标或插画。
  - 从上到下是标题、正文、按钮行。标题 `dialog-title` 只有一行，写成问句或动作，如「删除「示例订阅 A」？」。正文 `dialog-body` 是一两句后果说明。标题与正文相距 8px，正文与按钮行相距 20px。
  - 按钮行右对齐，用 36px 的标准按钮，间隔 8px。按钮顺序跟随平台：macOS 上取消在左、确认在右，Windows 上确认在左、取消在右。按钮文字写动作，如「删除」「保存」，不写「确定 / 是 / 否」；取消一律写「取消」。英文同理：写 "Delete"、"Save"，不写 "OK / Yes / No"，取消写 "Cancel"。
  - 每个对话框有且只有一个默认按钮，用 `button-primary` 样式，Enter 执行它。其余按钮是次要按钮或 `button-destructive`。
  - **普通确认框**：确认按钮是默认按钮。
  - **破坏性确认框**：确认按钮用 `button-destructive`，默认按钮是「取消」，所以「取消」是框里唯一的主色按钮。删除正在使用的对象时，正文写明后果，如「删除后改用「示例订阅 B」」。
  - **编辑对话框**用 `dialog-wide`，正文是一个 `code-editor`，「保存」是默认按钮。保存失败时不关闭对话框，编辑框进入错误状态，错误写在编辑框下方。
- **遮罩** `scrim`：只在模态对话框背后铺满窗口，不模糊背景。点击遮罩不关闭对话框。菜单和弹出层没有遮罩。
- **浮层的键盘行为**：
  - Esc 关闭最上层的浮层。在对话框里按 Esc 等于「取消」。
  - 打开时焦点移入浮层：菜单用 ↑ ↓ 移动高亮，Enter 或空格执行；普通确认框聚焦确认按钮；破坏性确认框聚焦「取消」；编辑对话框聚焦编辑框；含输入框的弹出层聚焦第一个输入框。
  - 模态对话框把焦点限制在内部：Tab 和 Shift+Tab 只在对话框的控件之间循环，到不了背后的页面。
  - Enter 执行默认按钮；用 Tab 把焦点移到其他按钮后，Enter 和空格执行聚焦的按钮。多行编辑框里 Enter 换行，Ctrl+Enter（macOS 上为 ⌘↩）执行默认按钮。
  - 浮层关闭后，焦点回到打开它的控件。
  - 打开时自动聚焦的按钮不显示焦点环，默认按钮靠主按钮样式辨认，用户按下 Tab 后照常显示；输入框和编辑框只要获得焦点就显示焦点环。
- **滚动条**：沿用 Qt 的细滑块，滚动时出现、停下后淡出，不加轨道底色。滑块颜色 `border-control`，按下时 `text-muted`。表格只在容器内部横向滚动时常驻显示。
- **焦点**：所有可交互元素在键盘聚焦时显示 2px `primary` 外环，外扩 2px；不能去掉焦点环。
- **禁用**：保留原样式，降低到 `opacity.disabled`（40%）不透明度，鼠标指针不变为手形。
- **空状态**：在容器里写一句简短的事实，如「没有匹配的连接」「未连接」。不放插画，不加引导性营销文案。
- **动效**：只用于状态切换反馈和浮层进出，不做页面转场、数字滚动或循环动画。
  - 开关圆钮、分段选中块：120ms ease-out。
  - 菜单、下拉菜单、弹出层：出现时不透明度 0 → 1，120ms ease-out；消失时 1 → 0，80ms ease-in。不位移，不缩放。
  - 对话框：出现时不透明度 0 → 1，同时从 98% 缩放到 100%，160ms ease-out；消失时不透明度 1 → 0，100ms ease-in，不缩放。遮罩随对话框一起淡入淡出。
  - 系统开启"减少动态效果"（macOS「减弱动态效果」，Windows「动画效果」关闭）时，以上全部改为即时切换，浮层直接出现和消失。

## Do's and Don'ts

### Do

- 先让用户一眼看到隧道状态、模式和当前节点，再给其他信息。
- 用设置式分组列表、表格和单行状态文字来组织信息。
- 让每个数字都有用途：速率、用量、延迟、连接列表都能直接指导下一步操作。
- 状态同时用文字和颜色表达；颜色对比满足 WCAG AA（正文 4.5:1，控件边界 3:1），浅色和深色两种外观各核对一遍。
- 数据和代码类内容（规则、版本、端口、日志）用等宽字体，保留原文。
- 新增组件前先复用这里已有的分组、行、分段控件、开关和表格。
- 破坏性操作先确认：确认框默认聚焦「取消」，标题写明对象，正文写清后果。

### Don't

- 不要做大号圆形连接按钮、居中的英雄区块或任何"控制中心"式布局。
- 不要用大号数字卡片堆指标（如"规则 1,286""DNS fake-ip"这类不指导操作的数字）。
- 不要做装饰性的柱状图、面积渐变、发光、毛玻璃或渐变背景。
- 不要用圆角卡片网格承载列表数据，不要给分组加阴影。
- 不要把对话框做成大圆角卡片，不要在提示框里放插画或大图标，不要把多个主色按钮或红色实心按钮叠在一起。
- 不要给浮层加毛玻璃或模糊背景，不要给菜单和弹出层加遮罩。
- 不要把状态做成彩色药丸或彩色横幅；不要靠红绿对比区分状态。
- 不要在标题下写解释性副标题，不要写营销语气的文案，不要使用 emoji。
- 不要超过一个强调色，也不要在一个页面里放多个主色按钮。
- 不要用反相、调暗或叠加透明度从浅色值推导深色；深色值只取 `colors.dark`。不要用纯黑做底色。
- 不要在实现中临时发明这份契约以外的颜色、圆角或间距；确有需要时先更新 DESIGN.md。
