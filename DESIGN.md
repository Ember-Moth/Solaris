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
---

# Mihomo Desktop

## Overview

Mihomo Desktop 是 Windows 与 macOS 上的 mihomo 图形客户端，界面用 PySide6 / QML 实现。用户主要做四件事：确认隧道是否在运行、切换出站模式和节点、管理订阅、排查连接和日志。界面要让这四件事一眼可见、一两下可改，其余时间保持安静。

视觉主题是**原生系统工具**：像 macOS「系统设置」或 Windows 11「设置」，而不是营销页或数据大屏。浅灰石墨底，内容放在白色分组里，组内用细线分行；全局只有一个信号蓝强调色，只用在开关开启、主操作、焦点和当前项标记上。信息密度偏高，控件紧凑，数字用等宽字体。

这份方向来自 Claude Design 原型的第二版（[画布](https://claude.ai/artifact/DVBcxqL7s2aX96F9enjwfj)）。第一版因为"AI 味太重"被否决，被删掉的做法列在 [Do's and Don'ts](#dos-and-donts) 里，是这份契约最重要的约束。

- **平台**：Qt Quick。文中 `px` 指 Qt 的设备无关像素，高分屏由 Qt 缩放。
- **窗口**：使用系统原生标题栏，不做无边框自绘窗口。
- **语言**：界面文案以简体中文为主，内核日志、协议名、规则等保持原文（英文）。
- **主题**：目前只定义浅色主题。深色主题尚未设计，实现时不要临时从浅色值推导；跟随系统深色模式之前需要先补齐这份契约。

## Colors

色彩分三层：窗口底色 `background` `#F3F4F6`，侧栏 `sidebar` `#E9EBEF`，内容容器 `surface` `#FFFFFF`。层级靠底色差和 1px 线条表达，不靠阴影。

- **强调色** `primary` `#1F5EFF`：开关开启、主按钮、焦点环、当前导航项图标、下载速率的线与箭头。悬停 `primary-hover` `#1B53E0`，按下 `primary-pressed` `#1749CC`；三者上的白字对比度分别为 5.1、6.3、7.3。每个页面最多一个主色按钮。
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

## Typography

拉丁字符用 **Inter**，数据用 **JetBrains Mono**，两者都是 OFL 许可，随应用打包（QML `FontLoader`），许可证文件一并分发。中文由系统字体回退：macOS 为 PingFang SC，Windows 为 Microsoft YaHei UI。Inter 不可用时回退到系统界面字体（SF Pro / Segoe UI Variable），JetBrains Mono 回退到 SF Mono / Consolas。

| 角色 | token | 用途 |
| --- | --- | --- |
| 页面标题 | `title` 18/600 | 每页只有一个，位于标题栏左侧 |
| 正文 | `body-md` 14/400 | 行标签、表格主列 |
| 强调正文 | `body-md-strong` 14/600 | 当前导航项、选中行 |
| 小正文 | `body-sm` 13/400 | 输入框文字、链接、次要列 |
| 按钮 | `label-md` 14/500 | 标准按钮、导航项 |
| 紧凑按钮 | `label-sm` 13/500，选中时 `label-sm-strong` 13/600 | 行内按钮、分段控件 |
| 说明 | `caption` 12/400 | 行内第二行说明、表头、图表刻度 |
| 分组标题 | `section-label` 12/600 | 分组上方的灰色小标题 |
| 数据 | `data-md` 13 / `data-sm` 12 等宽 | 速率、流量、延迟、端口、版本号、规则、日志 |

规则：

- 行高中文正文不低于 1.5，日志行 1.6；不对中文加字距。
- 会跳动的数字（速率、计时、计数）一律等宽，避免抖动；静态的小数字可以用 Inter，但要开启等宽数字特性 `tnum`。
- 一个界面最多三种字重：400、500、600。不使用 700 以上的粗体，也不用超过 18px 的字号。
- 文案以名词和短语为主，不写解释性副标题。需要说明时放在行内第二行，用 `caption`，不超过一句。

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

## Elevation & Depth

整体是平的。层级来自三件事：底色分层（窗口 → 侧栏 → 白色分组）、1px 线条、选中底色。

- 分组、表格、日志面板**没有阴影**，只有 `border` 外框。
- 只有两处使用阴影，都是为了表现"凸起的选中块"：
  - 分段控件的选中项：`0 1px 1px rgba(21, 23, 28, 0.12)`；
  - 侧栏的当前导航项：`0 1px 2px rgba(21, 23, 28, 0.08)`。
- 菜单、对话框、托盘弹出窗等浮层还没有设计；届时只允许新增一级浮层阴影，并在这里补充。

## Shapes

- **圆角**四档：
  - `xs` 4px：分段控件内的选项；
  - `sm` 6px：按钮、输入框、下拉框、分段控件底槽；
  - `md` 8px：分组、表格容器、日志面板、导航项；
  - `full`：开关、状态圆点、单选圈、进度条。

  嵌套时内层圆角等于外层减去内边距，例如分段控件底槽 6px、内边距 2px，选项 4px。
- **线宽**一律 1px；单选圈 1.5px。
- **图标**：线性图标，24 网格、2px 描边、圆角端点与拐角（与 Lucide 同一风格）。导航 18px，行内 14–16px。不使用填充图标、插画或 emoji。
- **侧栏左上角的标记**目前是占位图形，等产品命名后替换为正式标识。

## Components

- **侧栏导航**：
  - 导航项默认没有自己的底色（与侧栏同色），悬停 `nav-item-hover`。
  - 当前项使用白底、600 字重，图标改为 `primary`，并带 Elevation 中规定的轻阴影。
  - 侧栏底部用 8px 圆角的浅底块显示服务状态：状态圆点、一行状态文字、一行内核与协议版本。
- **页面标题栏**：见 Layout。连接状态放在概览页标题位置，写作「已连接 / 未连接」。连接与断开是一个普通按钮：已连接时为次要按钮「断开」，未连接时为主按钮「连接」。不做大号圆形开关。
- **分组与行**：
  - 分组标题用 `section-label`；可以在右侧放一个文字链接（如「管理」）。
  - 行的左侧是标签（可加一行 `row-secondary-text` 说明），右侧是值或控件。
  - 整行可点击时，悬停用 `row-hover`，选中用 `row-selected`，并在左侧加一个 `primary` 勾或单选圈。
- **按钮**：
  - 主按钮（`button-primary`）每页最多一个，用于该页最主要的创建或启动动作。
  - 其余按钮都是次要按钮（`button-secondary`），白底加 `border-button` 外框。
  - 行内操作用 `button-compact`；只有图标的操作用 `icon-button`（无边框，与所在行同底色），必须带无障碍名称。
  - 破坏性操作放进「更多」菜单，菜单项文字用 `danger`，不做红色实心按钮。
- **分段控件**：用于 2–4 个互斥选项（出站模式、排序、日志级别、TCP / UDP 筛选）。选中项用白底加 600 字重，不只靠底色区分。超过 4 个选项改用下拉框。
- **开关**：40 × 24 的轨道，开启用 `switch-on`，关闭用 `switch-off`；圆钮 18px，内缩 3px。开关只用于立即生效的布尔设置，并通过 `aria-checked` 表达状态。
- **输入框与下拉框**：白底加 `border-control` 外框。设置行里用 `input-compact`，表单里用 `input`。错误状态尚未设计，需要时文字用 `danger`、外框改 `danger`，并补进这份契约。
- **延迟**：用等宽文字显示数值，按阈值着色（`latency-good` < 150 ms、`latency-fair` 150–300 ms、`latency-poor` > 300 ms、`latency-timeout` 显示「超时」）。不用彩色药丸。
- **流量**：
  - 当前速率写成 `↓ 8.6 MB/s  ↑ 1.2 MB/s`，箭头用 `rate-download-glyph` / `rate-upload-glyph` 的颜色，数字用 `text`。
  - 趋势图是两条 1.5px 的折线，配浅色水平参考线，没有面积填充，也没有柱状图。
- **用量进度**：4px 高，底槽 `progress-track`，填充 `progress-fill`（中性灰），不用强调色，旁边写出具体数值。
- **表格**：表头 `table-header`；数值列用 `data-sm` 并右对齐；行操作（如关闭连接）是行尾的 `icon-button`。
- **日志**：等宽 `log-line`，三列依次是时间、级别、消息。级别只用文字颜色区分（`log-level-*`），不加底色。暂停状态写在标题栏的状态文字里，不弹出横幅。
- **焦点**：所有可交互元素在键盘聚焦时显示 2px `primary` 外环，外扩 2px；不能去掉焦点环。
- **禁用**：保留原样式，降低到 40% 不透明度，鼠标指针不变为手形。
- **空状态**：在容器里写一句简短的事实，如「没有匹配的连接」「未连接」。不放插画，不加引导性营销文案。
- **动效**：只用于状态切换反馈。开关圆钮、分段选中块的过渡为 120ms ease-out；不做页面转场、数字滚动或循环动画。系统开启"减少动态效果"时全部改为即时切换。

## Do's and Don'ts

### Do

- 先让用户一眼看到隧道状态、模式和当前节点，再给其他信息。
- 用设置式分组列表、表格和单行状态文字来组织信息。
- 让每个数字都有用途：速率、用量、延迟、连接列表都能直接指导下一步操作。
- 状态同时用文字和颜色表达；颜色对比满足 WCAG AA（正文 4.5:1，控件边界 3:1）。
- 数据和代码类内容（规则、版本、端口、日志）用等宽字体，保留原文。
- 新增组件前先复用这里已有的分组、行、分段控件、开关和表格。

### Don't

- 不要做大号圆形连接按钮、居中的英雄区块或任何"控制中心"式布局。
- 不要用大号数字卡片堆指标（如"规则 1,286""DNS fake-ip"这类不指导操作的数字）。
- 不要做装饰性的柱状图、面积渐变、发光、毛玻璃或渐变背景。
- 不要用圆角卡片网格承载列表数据，不要给分组加阴影。
- 不要把状态做成彩色药丸或彩色横幅；不要靠红绿对比区分状态。
- 不要在标题下写解释性副标题，不要写营销语气的文案，不要使用 emoji。
- 不要超过一个强调色，也不要在一个页面里放多个主色按钮。
- 不要在实现中临时发明这份契约以外的颜色、圆角或间距；确有需要时先更新 DESIGN.md。
