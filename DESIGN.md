---
version: alpha
name: Solaris
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
  caption-hover: "#15171C0F"
  caption-pressed: "#15171C0A"
  caption-close: "#C42B1C"
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
    caption-hover: "#FFFFFF0F"
    caption-pressed: "#FFFFFF0A"
    caption-close: "#C42B1C"
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
  page-bottom: 40px
  sidebar-width: 216px
  group-column-min: 400px
  window-default-width: 1280px
  window-default-height: 800px
  window-min-width: 960px
  window-min-height: 640px
  titlebar-height: 48px
  titlebar-page-top: 6px
  caption-button-width: 46px
  sidebar-top-macos: 52px
  sidebar-top-windows: 9px
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
  dialog-width-editor: 840px
  list-header-height: 28px
  insertion-line: 2px
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
  table-header-sorted:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.text}"
    typography: "{typography.caption}"
  sort-indicator:
    textColor: "{colors.text}"
    size: 12px
  header-status:
    textColor: "{colors.text-muted}"
    typography: "{typography.body-sm}"
  header-status-warning:
    textColor: "{colors.warning}"
    typography: "{typography.body-sm}"
  header-status-error:
    textColor: "{colors.danger}"
    typography: "{typography.body-sm}"
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
  search-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-md}"
    padding: 30px
  search-glyph:
    textColor: "{colors.text-muted}"
    size: 14px
  select:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    height: "{spacing.control-sm}"
    width: 120px
    padding: 10px
  select-hover:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.text}"
  select-pressed:
    backgroundColor: "{colors.surface-selected}"
    textColor: "{colors.text}"
  select-chevron:
    textColor: "{colors.text-muted}"
    size: 14px
  checkbox:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.xs}"
    size: 16px
  checkbox-checked:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.xs}"
    size: 16px
  drag-handle:
    textColor: "{colors.text-muted}"
    size: 16px
  insertion-line:
    backgroundColor: "{colors.primary}"
    height: "{spacing.insertion-line}"
  list-section-header:
    backgroundColor: "{colors.surface-subtle}"
    textColor: "{colors.text-muted}"
    typography: "{typography.section-label}"
    height: "{spacing.list-header-height}"
    padding: 10px
  list-item-compact:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.body-sm}"
    height: "{spacing.control-sm}"
    padding: 10px
  disclosure:
    textColor: "{colors.text}"
    typography: "{typography.label-sm}"
    size: 14px
  rule-match:
    textColor: "{colors.text-secondary}"
    typography: "{typography.data-sm}"
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
  menu-item-note:
    textColor: "{colors.text-muted}"
    typography: "{typography.caption}"
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
  dialog-editor:
    width: "{spacing.dialog-width-editor}"
  dialog-title:
    textColor: "{colors.text}"
    typography: "{typography.body-md-strong}"
  dialog-body:
    textColor: "{colors.text-secondary}"
    typography: "{typography.body-sm}"
---

# Solaris

## Overview

Solaris 是 Windows 与 macOS 上的 mihomo 图形客户端，界面用 PySide6 / QML 实现。用户主要做四件事：确认隧道是否在运行、切换出站模式和节点、管理订阅、排查连接和日志。界面要让这四件事一眼可见、一两下可改，其余时间保持安静。

视觉主题是**原生系统工具**：像 macOS「系统设置」或 Windows 11「设置」，而不是营销页或数据大屏。浅灰石墨底，内容放在白色分组里，组内用细线分行；全局只有一个信号蓝强调色，只用在开关开启、主操作、焦点和当前项标记上。信息密度偏高，控件紧凑，数字用等宽字体。

不该用的做法列在 [Do's and Don'ts](#dos-and-donts) 里，是这份契约最重要的约束。

- **平台**：Qt Quick。文中 `px` 指 Qt 的设备无关像素，高分屏由 Qt 缩放。
- **窗口**：不显示系统标题栏，侧栏和页面标题栏一直延伸到窗口上沿（见 Layout「顶部区域」）。macOS 保留系统的红绿灯；
  Windows 自己画最小化、最大化、关闭三个按钮。窗口的边框、阴影、贴靠和边缘缩放仍交给系统，不做完全无边框的窗口。
- **语言**：界面提供简体中文和英文。简体中文是源语言，英文是它的译文；默认跟随系统，系统首选语言是中文时用中文，其他一律用英文。内核日志、协议名、规则，以及配置里的组名和节点名保持原文，不翻译。英文的写法见 [英文文案](#英文文案)。
- **外观**：浅色和深色两套。默认跟随系统，系统在运行中切换外观时界面立即跟着变；也可以在设置里固定为浅色或深色。两套外观共用同一组语义角色和这份契约的全部规则，只换颜色取值（见 [深色](#深色)）；字体、间距、圆角和动效不变。窗口按钮随外观切换：macOS 的红绿灯由系统绘制，
  Windows 的三个按钮用 `caption-*` 颜色。

## Colors

颜色 token 按语义角色命名。frontmatter 里 `colors` 下直接列出的是浅色取值；`colors.dark` 为每个角色给出深色取值，键名一一对应。组件引用的 `{colors.X}` 在深色外观下取 `{colors.dark.X}`。增删角色时两组同时改，`scripts/gen_theme.py` 发现两组的键不一致就报错。下面先写浅色，深色见 [深色](#深色)。其他章节说的「白底」「白色分组」指 `surface`，「浅灰底」指 `surface-selected`，深色外观下取各自的深色值。

色彩分三层：窗口底色 `background` `#F3F4F6`，侧栏 `sidebar` `#E9EBEF`，内容容器 `surface` `#FFFFFF`。层级靠底色差和 1px 线条表达，不靠阴影。

- **强调色** `primary` `#1F5EFF`：开关开启、主按钮、焦点环、当前导航项图标、下载速率的线与箭头。悬停 `primary-hover` `#1B53E0`，按下 `primary-pressed` `#1749CC`；三者上的白字对比度分别为 5.1、6.3、7.3。每个页面、每个对话框最多一个主色按钮。
- **链接** `link` `#1846C7`，悬停 `link-hover` `#123A9E`。只用于跳转到其他页面的文字链接（如「管理」「全部 37 个」），以及跳到另一页同一操作的快捷方式（如概览页的「改用我的方案」）。唯一的例外是标题栏撤销提示里的「撤销」（见 Layout）。
- **文字**四级：`text` `#15171C` 正文与标题；`text-secondary` `#3D4350` 侧栏导航、等宽数值；`text-tertiary` `#4B5260` 未选中的分段项、次要数值；`text-muted` `#5F6673` 分组标题、说明文字、表头。`text-muted` 在最深的侧栏底色上仍有 4.8:1。
- **选中与悬停**：`surface-subtle` `#F7F8FA` 用于行悬停和表头；`surface-selected` `#ECEEF1` 用于选中行；`sidebar-hover` `#E0E3E8` 用于导航项悬停；`control-track` `#E3E5E9` 是分段控件的底槽。
- **线条**四种，按用途选，不混用：
  - `divider` `#ECEEF1`：组内行分隔、进度条底槽；
  - `border` `#DCDFE4`：分组外框、页面标题下方的分隔线、侧栏右边线；
  - `border-button` `#C9CDD4`：带文字的按钮外框，文字本身已经标明这是按钮，所以不要求 3:1；
  - `border-control` `#828893`：输入框、下拉框、单选圈和关闭状态的开关轨道。这些控件靠边框识别，在白底上 3.6:1、窗口底色上 3.2:1，满足非文字 3:1。
- **状态色只用于文字和小圆点**，不铺底色：
  - `success` `#0A6B5B`：服务运行中、延迟小于 150 ms；
  - `warning` `#8A5300`：延迟 150–300 ms、WARN 日志、需要留意的提示（如开启局域网连接）、日志的暂停状态；
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
- **Qt 控件的默认调色板**也从这组 token 取值，包括滚动条、文字选区和 Qt 自带的输入框右键菜单，映射见 `docs/ui.md` §6。

## Typography

拉丁字符用 **Inter**，数据用 **JetBrains Mono**，两者都是 OFL 许可，随应用打包在 `src/solaris/ui/fonts/`（QML `FontLoader`），各自的 `OFL.txt` 一并分发。只打包用到的字重：Inter 4.1 的 400、500、600，JetBrains Mono 2.304 的 400。中文由系统字体回退：macOS 为 PingFang SC，Windows 为 Microsoft YaHei UI。Inter 不可用时回退到系统界面字体（SF Pro / Segoe UI Variable），JetBrains Mono 回退到 SF Mono / Consolas。

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
- 行高按 CSS 的含义实现：每行高 = 字号 × 系数，如 `body-sm` 13 × 1.5 ≈ 20px、`caption` 12 × 1.4 ≈ 17px，会换行的文字用这个固定行高排版。Qt 的比例行高乘的是字体自身的行高（约为字号的 1.2 倍，中文回退字体更高），排出来比规定的松，不要用。
  - 单行文字保持字体自身的高度，在行和控件里垂直居中；行和控件的高度由 `row-*`、`control-*` 决定，不由文字撑开。
  - 并排的几列文字（如日志的时间、级别、消息）用同一个固定行高，首行才能对齐。
- 会跳动的数字（速率、计时、计数）一律等宽，避免抖动；静态的小数字可以用 Inter，但要开启等宽数字特性 `tnum`。
- 一个界面最多三种字重：400、500、600。不使用 700 以上的粗体，也不用超过 18px 的字号。
- 文案以名词和短语为主，不写解释性副标题。需要说明时放在行内第二行，用 `caption`，不超过一句。
- **定位中立**：Solaris 是网络代理客户端。文案只描述功能本身，用"代理""节点""订阅""规则""出口"这些中性的词；不提"机场"
  "翻墙""科学上网"一类说法，不暗示特定用途、地区或服务商。
- **只写结果，不讲实现**：说明文字告诉用户会发生什么、要注意什么，不解释内部做法（如"基础段""平台补丁""准入""拼装"）。
  对话框正文一般一两句；更多细节写进文档，不放进界面。

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

- **整体结构**：左侧固定宽度 `sidebar-width`（216px）侧栏，右侧内容区。内容区内边距上 `titlebar-page-top` 6px（见下文「顶部区域」）、左右 `page-x` 32px、下 `page-bottom` 40px。
- **顶部区域**：窗口最上面 `titlebar-height` 48px 是标题带，侧栏和内容区都从窗口上沿开始，没有单独的标题栏底色或分隔线。
  - 页面标题栏的垂直中线落在标题带中线（距上沿 24px）：内容区上内边距 `titlebar-page-top` 6px，36px 高的标题栏正好居中。
  - **macOS**：系统的红绿灯在侧栏左上角，由系统绘制和定位。侧栏上内边距 `sidebar-top-macos` 52px，标记和应用名排在红绿灯下面。
    红绿灯的位置以窗口的安全边距为准，不写死。
  - **Windows**：右上角三个按钮，各宽 `caption-button-width` 46px、高 48px，从右到左依次是关闭、最大化（窗口最大化时换成"还原"图形）、
    最小化；图形是 10px 的 1px 线条，颜色 `text`。悬停底色 `caption-hover`，按下 `caption-pressed`；关闭按钮悬停时底色
    `caption-close`、图形改白。页面标题栏右侧给三个按钮让出 138px 再加 8px 间隔。侧栏上内边距 `sidebar-top-windows` 9px，标记和应用名
    在标题带里垂直居中，与 Windows 应用在标题栏左侧显示图标和名称的习惯一致。
  - **拖动**：标题带里没有控件的地方都能拖动窗口，包括页面标题栏的空白处和侧栏顶部；双击按系统的设置缩放（macOS 遵循"连按窗口标题栏"）
    或最大化（Windows）。Windows 上最大化按钮要让系统识别为最大化按钮，悬停时才会弹出贴靠布局；Alt+空格的窗口菜单照常可用。
  - 读屏：窗口标题仍是应用名，三个按钮报作按钮，名称为"最小化""最大化"（或"还原"）"关闭"。
  - 键盘：Tab 在页面之后依次到达三个按钮，鼠标点击它们不移动焦点。按钮贴着窗口边缘，焦点环画在按钮内侧。
- **页面标题栏**：一行，左侧是 `title` 加一段 13px 的状态文字（`header-status`，如「已运行 2:14:36」「5 个代理组」），右侧依次是本页的筛选控件（搜索框、分段控件）和操作按钮。下方一条 `header-rule`，再隔 20px 开始内容。
  - 状态文字平时用 `text-muted`；操作失败时改写成错误原因，用 `danger`（`header-status-error`）；需要留意的状态用 `warning`（`header-status-warning`，如日志的「已暂停」）。不另弹横幅。
  - 宽度不够时，标题和状态文字至少留 280px。放不下「标题 + 状态 + 筛选控件 + 按钮」时，筛选控件整组移到 `header-rule` 下方单独一行，左对齐，与上下内容各隔 20px；按钮留在标题栏。
  - 状态文字放不下时用省略号截断；标题、按钮和分段控件的文字不截断，也不换行。
  - **页面级开关**：决定整页显示什么的二选一（如分流页的「订阅自带 / 我的方案」）是一个分段控件，放在标题栏最右侧，不和筛选控件放在一起，窄窗口下也不移到下一行。它改的是全局状态，所以切换立即生效；页面里的其他控件都随它变化。每页最多一个。
  - **撤销提示**：删除列表里的条目后，状态文字的位置改写成「已删除「Telegram」 · 撤销」，持续 8 秒，或者到下一次改动为止，然后恢复原来的状态文字。文字用 `text-secondary`，「撤销」是 `link` 色的文字按钮（`link` 只用于跳转的例外），报作按钮，名称「撤销删除「Telegram」」；整段报作状态（`role=status`），出现时读屏读出。不另弹浮层或横幅。
- **分组**：分组标题与分组间隔 8px；分组之间上下 20px、左右 24px。宽度足够时分组排成两列，每列不窄于 `group-column-min`（400px）；内容区放不下两列时退为一列，不出现横向滚动。
- **行高**：单行行 `row-single` 44px，双行行（主文字加说明）`row-double` 52px，表头 `row-header` 32px。行内左右内边距 16px。
- **控件高度**：标准 `control-md` 36px（标题栏按钮、表单输入），紧凑 `control-sm` 32px（行内按钮、分段控件、设置里的下拉框和数字框）。
- **点击区域**：桌面端以鼠标为主，36 / 32px 的控件可以接受；但整行可点击的列表项高度不小于 44px，开关和单选圈的可点范围要延伸到整行标签。
- **列表与表格**优先于卡片网格。表格放在分组容器里，列宽不够时只在容器内部横向滚动；主列用省略号截断，数值列右对齐。
- **间距**：以 4px 为基数，只用 4、8、12、16、20、24、32、40。
- **浮层位置**：
  - 菜单和弹出层在触发控件下方打开，间隔 `overlay-gap` 4px。「更多」菜单的右边缘与触发按钮对齐；下拉菜单左对齐，宽度不小于下拉框。下方放不下时改到上方；上下都放不下时放在空间较大的一侧，再按下一条限制在窗口内，内容在浮层里滚动。
  - 任何浮层与窗口边缘至少相距 `overlay-margin` 8px，不超出窗口。
  - 菜单宽度不小于 `menu-min-width` 180px，随最长的项变宽；弹出层宽度在 `popover-min-width` 240px 到 `popover-max-width` 360px 之间。
  - 对话框在窗口内水平、垂直居中。宽度分三档：普通的 `dialog-width` 400px；含编辑框或一个列表的 `dialog-width-wide` 640px（如覆写编辑、添加规则）；左边编辑、右边实时预览并排的 `dialog-width-editor` 840px（`dialog-editor`，如策略组编辑器）。840px 的对话框在窗口放不下两栏时把预览移到条件下方，宽度仍不超过窗口减去两侧 `overlay-margin`。高度随内容，最多为窗口高度减去上下各 40px，超出时只滚动正文，标题和按钮行不动。

## Elevation & Depth

整体是平的。层级来自三件事：底色分层（窗口 → 侧栏 → 白色分组）、1px 线条、选中底色。

- 分组、表格、日志面板**没有阴影**，只有 `border` 外框。
- 页面内只有两处使用阴影，都是为了表现"凸起的选中块"：
  - 分段控件的选中项：`0 1px 1px`，颜色 `shadow-segment`（浅色即 `rgba(21, 23, 28, 0.12)`）；
  - 侧栏的当前导航项：`0 1px 2px`，颜色 `shadow-nav`（浅色即 `rgba(21, 23, 28, 0.08)`）。
- 浮层（菜单、下拉菜单、弹出层、对话框）共用唯一一级浮层阴影 `overlay-shadow`：向下偏移 `overlay-shadow-y` 4px，模糊 `overlay-shadow-blur` 16px，颜色 `shadow-overlay`，浅色即 `0 4px 16px rgba(21, 23, 28, 0.12)`；再加 1px `border` 外框。浮层之间不再分级。
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
- **线宽**一律 1px；单选圈和复选框 1.5px。
- **图标**：线性图标，24 网格、2px 描边、圆角端点与拐角（与 Lucide 同一风格）。导航 18px，行内 14–16px。不使用填充图标、插画或 emoji；
  唯一的例外是品牌标记的实心内核。
- **品牌标记"日冕"**：实心的圆形内核，外面一圈在右上方留缺口的圆环（24 网格：内核半径 3.6，圆环半径 8，从 -40° 顺时针到
  250°，缺口 70°）。侧栏左上角的 30px `text` 色方块里画 `surface` 色的单色标记（浅色外观下是深底白标）；应用图标是石墨底（`#15171C`）的圆角方块、白色圆环和
  `primary` 蓝色内核，macOS 按 1024 画布上 824 的网格留边，32px 及以下加粗圆环和内核。所有尺寸由
  `scripts/gen_icons.py` 从同一组几何参数生成，母版在 `ui/brand/`，`.icns`、`.ico` 在 `package/icons/`。

## Components

- **侧栏导航**：
  - 页面依次是概览、代理、订阅、分流、连接、日志、设置（英文 "Overview"、"Proxies"、"Subscriptions"、"Routing"、"Connections"、"Logs"、"Settings"）。「订阅」只管来源，「分流」决定用哪套规则。
  - 应用名下面一行 `caption` 写当前在用什么：「「日常」方案」或「「示例订阅 A」的规则」，与概览页「当前使用」一致。
  - 导航项默认没有自己的底色（与侧栏同色），悬停 `nav-item-hover`。
  - 当前项使用白底、600 字重，图标改为 `primary`，并带 Elevation 中规定的轻阴影。
  - 侧栏底部用 8px 圆角的浅底块显示服务状态：状态圆点、一行状态文字、内核与协议版本（中文一行，英文分两行，见「英文文案」）。
- **托盘图标**：
  - 图形是单色的"日冕"标记，画在 20 单位的视框里，圆环描边 2.4，比界面图标粗，在 16–22px 的托盘尺寸下仍清楚；导出为 32px 的 SVG，放在 `ui/tray/`，由 `scripts/gen_icons.py` 生成。
  - macOS 用模板图（黑色加不透明度），由系统按菜单栏外观着色。Windows 按任务栏的深浅各备一套：浅色任务栏用浅色外观的 `text`（`#15171C`），深色任务栏用白色。任务栏的深浅取系统的「默认 Windows 模式」，不取应用的外观，应用固定为深色时托盘图标也不跟着变。
  - 隧道未运行时整个图形 45% 不透明。不加彩色角标、状态圆点或第二种图形。
  - 提示文字写「应用名 · 状态」，如「Solaris · 已连接」。
  - 托盘菜单是系统原生菜单，外观跟随平台，不套用下文应用内菜单的样式。依次是：状态（不可点）、连接 / 断开、「出站模式」子菜单（单选）、打开主窗口、「退出界面（隧道保持运行）」，各组之间用分隔线。子菜单只出现在这里，是「不做多级子菜单」的例外。
  - Windows 上单击或双击图标打开主窗口，右键打开菜单；macOS 上单击打开菜单。
- **页面标题栏**：见 Layout。连接状态放在概览页标题位置，写作「已连接 / 未连接」，状态文字写运行时长。标题栏里不放连接按钮。
- **连接开关**：概览页「出站」分组的第一行，标签「连接」，右侧是普通开关。这是界面上唯一的连接控件，概览页不再放
  「TUN 模式」「系统代理」开关。打开即连接，关闭即断开；正在连接或断开时开关不可操作，这一行第二行用 `caption` 写
  「正在连接…」「正在断开…」；连不上时开关回到关闭，第二行用 `danger` 写失败原因。读屏报作开关，名称「连接」。
  托盘菜单里的「连接 / 断开」照旧保留。不做大号圆形开关，也不把连接做成主色按钮。
  - 连接时怎样接管流量由设置页「接管方式」决定（分组标题右侧写「连接时使用」）：「TUN 模式」「系统代理」两个开关，
    默认都打开。macOS 上 TUN 是必选的，这一行不放开关，右侧写「已启用」，第二行写「macOS 上总是启用」；macOS
    不设系统代理，没有这一行。已连接时改这两项立即生效。两项都关闭时连接只开放混合端口，系统代理一行第二行写
    「两项都关闭时，连接后只开放混合端口」。开发模式两行右侧都写「不使用」，分组标题右侧写「开发模式只开放混合端口」。
  - 已连接时，概览页标题栏的状态文字写当前的接管方式和运行时长，如「TUN 与系统代理 · 已运行 2:14:36」；只开一项时写
    「TUN」或「系统代理」，两项都没开时写「混合端口 7890」。未连接时标题栏只写「未连接」。
- **分组与行**：
  - 分组标题用 `section-label`；可以在右侧放一个文字链接（如「管理」）。
  - 行的左侧是标签（可加一行 `row-secondary-text` 说明），右侧是值或控件。
  - 整行可点击时，悬停用 `row-hover`，选中用 `row-selected`，并在左侧加一个 `primary` 勾或单选圈。
  - 行的附加状态（如订阅「已覆写」）写进第二行说明，用「 · 」与其他信息隔开，不加徽章或彩色标记。
  - 说明里需要留意的内容可以用 `warning` 色写一段（`notice-warning`）：整句都是提示时整句用 `warning`，如开启局域网连接后的「同一网络的设备可以使用混合端口」；与其他说明同在一行时只给最后一段上色，前面用「 · 」隔开，如「订阅 · 9 个节点 · 更新失败：HTTP 503，沿用上次的节点」。一行最多一段，不加图标或底色；错误用 `danger`，不用 `warning`。表格单元格同理，「已过期」「更新失败」整格用 `warning`。
- **按钮**：
  - 主按钮（`button-primary`）每页最多一个，用于该页最主要的创建或启动动作；对话框里的主按钮是它的默认按钮（见下文「对话框」）。
  - 其余按钮都是次要按钮（`button-secondary`），白底加 `border-button` 外框。
  - 行内操作用 `button-compact`；只有图标的操作用 `icon-button`（无边框，与所在行同底色），必须带无障碍名称。
  - 破坏性操作放进「更多」菜单（`menu-item-destructive`），执行前弹出破坏性确认框；不做红色实心按钮。
  - 破坏性确认框里的确认按钮用 `button-destructive`：白底、`border-button` 外框、`danger` 文字，悬停和按下的底色与次要按钮相同。它只出现在确认框里。
- **分段控件**：用于 2–4 个互斥选项（出站模式、排序、日志级别、TCP / UDP 筛选）。选中项用白底加 600 字重，不只靠底色区分。超过 4 个选项改用下拉框。键盘上每一段是一个 Tab 停靠点，空格选中。
  - 作为页面级开关时放在标题栏最右侧（见 Layout）。
  - 切换同一块内容的两三个视图时（如方案的「规则 / 策略组」）也用它，放在这块内容上方的控件行里；读屏报作标签栏，每一段是标签页。
- **开关**：40 × 24 的轨道，开启用 `switch-on`，关闭用 `switch-off`；圆钮 18px，内缩 3px。开关只用于立即生效的布尔设置，并通过 `aria-checked` 表达状态。
- **复选框**：用于表单里提交时才生效的选择，如导入表单的「每 12 小时自动更新」；立即生效的设置用开关。16px 方框、4px 圆角：未勾选时 `checkbox`（`surface` 底、1.5px `border-control` 外框），勾选时 `checkbox-checked`（`primary` 底、12px `on-primary` 勾，无外框）。文字在方框右侧 8px，`body-sm`、`text-secondary`，点击文字也能切换。
  - **列表行里的启用复选框**是例外：规则列表每行行首的方框表示「启用」，勾选立即生效（与 macOS 的列表一致，一行一个开关太重）。它不带文字，读屏名称写「启用「Telegram」」；停用的行主文字改为 `text-muted`，说明末尾加「 · 已停用」，目标列照常显示。
  - **多选列表**里每项前面是复选框，整行可点（见「可搜索列表」）。
- **输入框**：`surface` 底（浅色下为白底）加 `border-control` 外框。设置行里用 `input-compact`，表单里用 `input`。错误状态：外框改为 `danger`，下方用 `input-error`（`caption`、`danger`）原样写出错误，不加图标。选中的文字用 `primary` 底、`on-primary` 字，输入框和 YAML 编辑框相同。
- **搜索框** `search-field`：36px 的输入框，左侧 10px 处是 14px 的放大镜（`search-glyph`，`text-muted`），文字从 30px 处开始。
  - 占位文字写明搜什么，如「搜索主机、进程或规则」，它同时是搜索框的无障碍名称；宽度至少放得下占位文字（连接页不窄于 220px，日志页不窄于 200px）。
  - 边输入边筛选，不设搜索按钮和清除按钮。Esc 清空已输入的文字；框里没有文字时 Esc 交给外层处理。
  - 放在标题栏的筛选位置、分段控件之前，标题栏放不下时随筛选控件一起换到下一行（见 Layout）。
- **下拉框** `select`：自绘的按钮，32px 高、`border-control` 外框、6px 圆角，左内边距 10px、右内边距 8px；左侧是当前选项（`body-sm`、`text`，放不下时截断），右侧 8px 外是 14px 的向下箭头（`select-chevron`，`text-muted`）。最窄 120px。悬停 `select-hover`、按下 `select-pressed`，与次要按钮相同。
  - 点击或空格打开它的下拉菜单（见「菜单」）：左对齐、不窄于下拉框，当前选项带勾；选中后菜单关闭，焦点回到下拉框。
  - 选项超过 4 个，或选项文字太长、放不进分段控件时用下拉框（如「关闭窗口时」）。
  - 读屏把它报作下拉框（combobox）：名称是所在行的标签，描述是当前选项。
  - 选项可以带 `menu-item-note` 注记（见「菜单」），不可选的项禁用并写明原因，如订阅下拉框里的「只有节点」。
- **目标下拉框**：规则和兜底「其他流量」选择流量去向的下拉框，选项分段固定，段之间用 `menu-separator` 隔开，空的段连同它前面的分隔线一起省略：
  1. 内置：节点选择、自动选择；
  2. 地区：节点池里有节点的地区，按地区表顺序，用本地化的地区名（不带旗帜）；隐藏的地区组不出现；
  3. 自建策略组：按方案里的顺序；
  4. 组网出站：节点池里的 Tailscale、ZeroTier、EasyTier 出站（只在"我的方案"下）；
  5. 链式出口：WARP，方案没有打开它时禁用并注明"未启用"；
  6. 直连、拒绝。

  当前目标是暂时没有节点的地区时，它仍显示在按钮上，但不出现在选项里；按钮下方或旁边用 `warning` 色的 `caption` 写「无节点，暂走节点选择」。超过 10 项时菜单内滚动（与下拉菜单相同）。
- **YAML 编辑框** `code-editor`：多行等宽文本框，`data-md` 文字、`border-control` 外框、6px 圆角、10px 内边距，最小高度 `editor-min-height` 240px，内容更长时在框内滚动；不自动换行，长行横向滚动。错误状态与输入框相同。
- **延迟**：用等宽文字显示数值，按阈值着色（`latency-good` < 150 ms、`latency-fair` 150–300 ms、`latency-poor` > 300 ms、`latency-timeout` 显示「超时」）。不用彩色药丸。
- **流量**：
  - 当前速率写成 `↓ 8.6 MB/s  ↑ 1.2 MB/s`，箭头用 `rate-download-glyph` / `rate-upload-glyph` 的颜色，数字用 `text`。
  - 趋势图是两条 1.5px 的折线，配浅色水平参考线，没有面积填充，也没有柱状图。
- **用量进度**：4px 高，底槽 `progress-track`，填充 `progress-fill`（中性灰），不用强调色，旁边写出具体数值。
- **表格**：表头 `table-header`；数值列用 `data-sm` 并右对齐；行操作（如关闭连接）是行尾的 `icon-button`。
  - **排序**：可排序的列，表头是按钮。当前排序列的表头文字用 `text`（`table-header-sorted`），后接 12px 箭头（`sort-indicator`）：↓ 表示从大到小，↑ 表示从小到大。右对齐的数值列把箭头放在文字左侧，让文字与下面的数值右边对齐。未排序的表头 `text-muted`，悬停变 `text`。点新的一列先按从大到小排，再点同一列反向。读屏名称带上方向，如「速率，从大到小」。
  - **速率和流量**写成两行：上一行 ↓ 下载，下一行 ↑ 上传；箭头用 `rate-download-glyph` / `rate-upload-glyph` 的颜色，数值用 `data-sm`、`text-secondary`，箭头与数值相隔 4px，整列右对齐。这样的行用 `row-double`。概览等单行位置仍写成一行（见「流量」）。
  - **选中与键盘**：表格整体是一个 Tab 停靠点，焦点环画在表格容器上（8px 圆角）；行本身和行尾的图标按钮不进 Tab 顺序。↑ ↓ 移动选中行，Page Up / Page Down 翻一屏，Home / End 到首尾，选中行始终滚动到可见处；Enter 或空格打开选中行的详情弹出层；Delete（或 Backspace）执行行尾操作。可排序的表头按钮各是一个停靠点。
  - 选中行用 `row-selected`，主列文字改为 600 字重；鼠标点击一行即选中并打开详情。详情关闭后焦点回到表格。
- **只读规则表**：显示订阅自带的规则，用在分流页的「订阅自带」。两列：匹配（`rule-match`：`data-sm`、`text-secondary`，原样写内核的规则，如 `GEOSITE,youtube`，带 `no-resolve` 时照写）和目标（`body-sm`、`text`，策略组名原文）。行高 36px，`divider` 分隔，没有悬停、选中和行内操作；放不下时省略号截断，不换行。上方的分组标题右侧写条数（「412 条」，搜索时「匹配 12 条」）；搜索框在页面的控件行里，不在表内。整张表是一个 Tab 停靠点（同「表格」的键盘规则，但没有选中行），读屏报作表格，每行读作「匹配，目标」。
- **可排序列表**：规则列表这类由用户决定顺序的列表。
  - 每行最左侧是 16px 的拖动手柄（`drag-handle`：六点图形，`text-muted`），按住手柄拖动整行；拖动时被拖的行降到 40% 不透明度，落点处画 2px 的插入线（`insertion-line`：`primary`，左右各缩进 12px），放开后移到插入线的位置。手柄只是鼠标的抓手，不进 Tab 顺序，读屏忽略。
  - 键盘用每行「更多」菜单里的「上移」「下移」代替拖动，第一行的「上移」、最后一行的「下移」禁用。移动后焦点跟着这一行。
  - 搜索或筛选时列表只显示一部分，拖动禁用：手柄降到 40% 不透明度，不能按住；「上移」「下移」仍按完整列表的顺序移动。
  - 固定在末尾的行（如「其他流量」）没有手柄和复选框，不能拖动，也不能把别的行拖到它下面。
  - 连续的拖动和上下移动停止约 1 秒后合并成一次生效，期间列表已经按新顺序显示。
- **可搜索列表**：对话框里从几十到上千项中挑选的列表，如添加规则的「服务」「分类」。
  - 上方是 36px 的搜索框，边输入边筛选，不区分大小写；下方是固定高度的列表容器（`border` 外框、6px 圆角），内容在容器里滚动。
  - 列表项 `list-item-compact`：32px 高，复选框（多选）或勾（单选）在前，名称在后，右侧可以有 `caption` 注记；整行可点。
  - 按类别分组时，类别标题 `list-section-header`（28px、`surface-subtle` 底、`section-label` 字）在滚动时吸顶，直到下一个标题把它推走。类别标题不能选，读屏报作标题。
  - 已经在用的项禁用（40% 不透明度）、保持勾选，注记写「已添加」；编辑一条已有规则时，它自己的项照常可选。
  - 列表下方用一行 `caption` 写已选的结果（「已选 2 个：YouTube、Twitch · 将添加 2 条规则」）或提示；没有匹配项时列表里写「没有匹配的服务」。
  - 键盘：搜索框之后整个列表是一个 Tab 停靠点，↑ ↓ 移动高亮（跳过类别标题和禁用项），空格勾选或取消；在搜索框里按 ↓ 进入列表。
- **条件编辑表单**：对话框里描述「匹配什么、去哪里」的表单，如添加规则、策略组编辑器。
  - 从上到下：种类的分段控件（如「服务 / 分类 / 域名 / IP 段」，左对齐，不铺满）、该种类的输入区、附加选项（如「不解析域名（no-resolve）」复选框）、目标。行与行之间 16px。
  - 标签在控件上方时用 `caption`、`text-tertiary`，相隔 6px；与控件同行时用 `body-sm`、`text-secondary`，在控件左侧 12px。
  - 多行文本输入（域名、IP 段）用 `code-editor` 的样式，最小高度 168px；每行一项，前缀写在说明里（「直接写域名是后缀匹配，= 开头是完整匹配，~ 开头是关键字」）。编辑框下方实时写识别结果（「识别出 12 个，其中 1 行无效」），有无效行时编辑框进入错误状态，下方用 `input-error` 写第一处无效行（「第 3 行无效：exa mple.com」），确认按钮禁用。
  - 切换种类时保留各种类已经输入的内容，直到对话框关闭。
  - 确认按钮在什么都没选、没填时禁用；内核拒绝时（`validate` 失败）对话框不关闭，错误用 `input-error` 写在按钮行上方。
- **可折叠区**：把进阶内容收起来的一段，如策略组编辑器的「更多条件」「高级」。
  - 标题是一个 `disclosure` 文字按钮：14px 的向右箭头（展开时转向下，120ms），后接 `label-sm` 文字；已经设置过的内容写在标题里（「更多条件（已设置 2 项）」），收起时也看得到。标题上方用一条 `divider` 与前面的内容隔开，展开的内容与标题相隔 12px。
  - 已经设置过内容的区默认展开，其余默认收起。读屏报作按钮，带展开状态；空格或 Enter 切换。
  - **代理页的「地区」小节**用同一种按钮：「我的方案」下，策略组列表里自动生成的地区组收进一个「地区」行，默认收起，行右侧写地区组的个数；展开后地区组缩进 12px 列在它下面。当前选中的组在小节里时，小节自动展开。
- **日志**：等宽 `log-line`，三列依次是时间、级别、消息。级别只用文字颜色区分（`log-level-*`），不加底色。
  - 暂停状态写在标题栏的状态文字里，用 `warning` 色（`header-status-warning`）：「已暂停」，暂停期间有新日志时写「已暂停 · 12 条新日志」。不弹出横幅；出错时状态文字改用 `danger`。
  - 日志面板是一个 Tab 停靠点，焦点环画在面板上：↑ ↓ 滚动一行，Page Up / Page Down 和空格翻一屏，Home / End 到首尾。滚到底部时跟随新日志，向上滚动后停在原处。
- **菜单**（「更多」菜单、下拉菜单）参照 Windows 11 的上下文菜单与 macOS 的菜单：
  - 容器 `menu`：白底、1px `border`、8px 圆角、4px 内边距，带浮层阴影。
  - 菜单项 `menu-item`：32px 高、4px 圆角、`body-sm` 文字，左右内边距 8px，左侧留 14px 的勾选位。可勾选的项勾上时显示 `menu-check`：14px 的 `primary` 勾。
  - 悬停和键盘高亮都用 `menu-item-highlighted`：`surface-selected` 浅灰底，文字不变色。不用强调色铺底。
  - 破坏性的项用 `menu-item-destructive`，高亮时仍是 `danger` 文字（`menu-item-destructive-highlighted`）。它放在菜单最后，前面用 `menu-separator` 隔开：1px `divider`，上下各留 4px。
  - 菜单项只有文字：不加图标或快捷键提示，不做多级子菜单。打开对话框继续输入的项以「…」结尾（如「编辑覆写…」）；只弹出确认框的项不加（如「删除」）。
  - **注记** `menu-item-note`：菜单项右侧可以有一段 `caption`、`text-muted` 的注记，与文字相隔至少 12px、右对齐，只写帮助选择的事实，如订阅下拉框里的「412 条规则」「只有节点」。注记不截断；菜单随它变宽。读屏把注记读作这一项的描述。
  - **链接型菜单项**：跳到另一页的项（如订阅「更多」菜单里的「使用它的规则」）外观与普通项相同，不用 `link` 色，也不以「…」结尾；执行后菜单关闭、切到目标页，焦点交给目标页的标题栏控件。
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
  - 模态对话框把焦点限制在内部：Tab 和 Shift+Tab 只在对话框的控件之间循环，到不了背后的页面。对话框显示期间，焦点也不会被交回页面，包括从菜单项打开对话框、菜单还在淡出的时候。
  - Enter 执行默认按钮；用 Tab 把焦点移到其他按钮后，Enter 和空格执行聚焦的按钮。多行编辑框里 Enter 换行，Ctrl+Enter（macOS 上为 ⌘↩）执行默认按钮。
  - 浮层关闭后，焦点回到打开它的控件，焦点环显示与否也与打开前相同。从菜单项打开的对话框，关闭后焦点回到菜单的触发按钮。打开它的控件已经不在时（如刚删除的那一行），焦点交给接替它的控件，见「焦点与键盘」。
  - 打开时自动聚焦的按钮不显示焦点环，默认按钮靠主按钮样式辨认，用户按下 Tab 后照常显示；输入框和编辑框只要获得焦点就显示焦点环。
- **滚动条**：沿用 Qt 的细滑块，滚动时出现、停下后淡出，不加轨道底色。滑块颜色 `border-control`，按下时 `text-muted`。表格只在容器内部横向滚动时常驻显示。
- **焦点与键盘**：只用键盘能完成全部操作。
  - 所有可交互元素在键盘聚焦时显示 2px `primary` 外环，外扩 2px；不能去掉焦点环。按钮类控件被鼠标点击得到的焦点不显示焦点环；输入框和编辑框只要有焦点就显示。
  - Tab 顺序与阅读顺序一致：侧栏导航，标题栏（筛选控件、按钮），然后是内容区。分组成行排列时（如概览）逐行先左后右，分成两个独立的列时（如设置）先左列后右列；组内从上到下，行内从左到右。
  - 每个控件是一个停靠点。整行可点的列表（代理组、节点）每行是一个停靠点，行里的按钮紧跟在这一行之后；↑ ↓ 在行之间移动焦点，空格执行这一行（选中代理组或节点），只移动焦点不会切换。表格和日志面板整体只占一个停靠点（见「表格」「日志」）。
  - 获得焦点的控件连同焦点环要完整可见：页面和表格自动滚动，在控件四周留出 12px；比可见区域还宽的行只保证左端可见。
  - 列表数据刷新时行原地更新，焦点留在原来的行上。焦点所在的行被删除时，焦点移到接替它位置的行；删除的是最后一行时移到上一行；一行也不剩时移到本页的主操作（如「从链接导入」）。内联表单收起后，焦点回到打开它的按钮。
  - macOS 上按钮类控件能否用 Tab 到达，跟随系统设置里的「键盘导航」，与原生应用一致；输入框、列表和表格始终可以到达。Windows 上 Tab 到达所有控件。
- **读屏**：键盘焦点到哪里，读屏就能读出那是什么、处于什么状态。
  - 每个控件都有名称和正确的角色。名称写控件对应的标签，如开关的名称是所在行的「TUN 模式」。只有图标的按钮写明动作和对象，如「示例订阅 A 的更多操作」「关闭 www.example.com 的连接」；按钮文字只有动词时在名称里补上对象，如「更新 示例订阅 A」。
  - 整行可点的列表行报作单选按钮：名称是主文字，描述是其他各列，如节点「香港 01」的描述「订阅 A，Trojan，延迟 68 ms」，当前项为选中。订阅页的行不可点，报作列表项。表格行报作行，名称是主列，描述是其他各列。
  - 分段控件报作一个分组，名称是它的标签，每一段是单选按钮；开关报作复选框，带开关状态；下拉框见上文；流量图是图形，描述写当前速率；用量条是进度条，描述写已用的百分比；日志每行读作一条，包含时间、级别和消息。
  - 页面标题、分组标题和对话框标题报作标题（heading）。标题栏状态、服务状态、错误、空状态和「关于」里的数值报作静态文字；行内说明同时写进对应控件的描述，如端口错误、局域网连接的提示。
  - 只起装饰作用的图形不进读屏：状态圆点、单选圈、图标，以及旁边已有文字说明的 ↓ ↑ 箭头。
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
- 只用键盘能完成每一项操作，每个控件都有读屏名称；删除、刷新或关闭浮层之后，焦点落在用户预期的位置。

### Don't

- 不要做大号圆形连接按钮、居中的英雄区块或任何"控制中心"式布局；连接只是出站分组里的一个开关。
- 不要用大号数字卡片堆指标（如"规则 1,286""DNS fake-ip"这类不指导操作的数字）。
- 不要做装饰性的柱状图、面积渐变、发光、毛玻璃或渐变背景。
- 不要用圆角卡片网格承载列表数据，不要给分组加阴影。
- 不要把对话框做成大圆角卡片，不要在提示框里放插画或大图标，不要把多个主色按钮或红色实心按钮叠在一起。
- 不要给浮层加毛玻璃或模糊背景，不要给菜单和弹出层加遮罩。
- 不要把状态做成彩色药丸或彩色横幅；不要靠红绿对比区分状态。
- 不要在标题下写解释性副标题，不要写营销语气的文案，不要使用 emoji。
- 不要在界面里提到"机场""翻墙"一类说法，也不要在说明文字里解释内部实现。
- 不要超过一个强调色，也不要在一个页面里放多个主色按钮。
- 不要用反相、调暗或叠加透明度从浅色值推导深色；深色值只取 `colors.dark`。不要用纯黑做底色。
- 不要在实现中临时发明这份契约以外的颜色、圆角或间距；确有需要时先更新 DESIGN.md。
- 不要让焦点在列表刷新、删除一行或关闭浮层后掉回窗口，也不要用整行重建的方式刷新列表。
- 不要做完全无边框的窗口，也不要自己画窗口阴影、边框或红绿灯；贴靠、缩放和窗口菜单必须保持系统原样。
