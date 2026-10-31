# mihomo 内核扩展与配置指南

本文面向使用 Solaris 的用户，介绍我们维护的 [mihomo fork](https://github.com/Ember-Moth/mihomo) 中需要用户配置的客户端扩展。
示例以 `Meta` 提交 `56c6fc1d5c27` 为核对基准；Solaris 当前锁定版本见 [go.mod](../src/core/go.mod)。
源码主分支和已发布安装包可能使用不同内核，请在「设置 → 关于 → 内核」及对应版本的发布说明中核对支持情况。

| 能力 | 主要变化 | 服务端要求 |
| --- | --- | --- |
| Naive | `type: naive`，HTTP/2、HTTP/3/QUIC、可选 UoT | Naive 兼容服务端；QUIC、UoT 分别需要服务端支持 |
| AnyTLS + REALITY | AnyTLS 节点可以使用 `reality-opts` | 同时支持 AnyTLS 协议和 REALITY 的端点 |
| REALITY 握手 | 版本标识对齐 `26.10.29`，默认保留指纹中的 X25519MLKEM768 | 服务端的版本策略和密钥交换能力需要匹配 |
| Snell v4–v6 | v4/v5 使用 sing-snell，新增 v6 模式和 `user-key`；v1–v3 保留 | 与客户端相同的版本、PSK、模式及用户密钥设置 |

Solaris 的安装包按发布配置启用 Naive。自行编译普通无标签 Go 开发服务时，Naive 不可用；构建方式见
[开发文档](development.md#从源码构建)。

## 先导入一个完整示例

下面的文件都是**配置模板**，不是可直接连接的公共节点。必须替换 `proxy.example.com`、`cover.example.com`、
`CHANGE_ME_…` 等示例值，使用你自己的服务端地址、端口、凭据和密钥。REALITY 公钥占位符在替换前会被校验拒绝。

| 完整 YAML | 用途 |
| --- | --- |
| [naive-http2.yaml](examples/mihomo-fork/naive-http2.yaml) | 默认 HTTP/2 Naive |
| [naive-quic.yaml](examples/mihomo-fork/naive-quic.yaml) | Naive HTTP/3/QUIC |
| [naive-uot.yaml](examples/mihomo-fork/naive-uot.yaml) | Naive + UDP-over-TCP v2 |
| [anytls-reality.yaml](examples/mihomo-fork/anytls-reality.yaml) | AnyTLS + REALITY，使用当前默认密钥交换行为 |
| [anytls-reality-legacy.yaml](examples/mihomo-fork/anytls-reality-legacy.yaml) | AnyTLS + REALITY，显式使用旧服务端兼容开关 |
| [snell-v4.yaml](examples/mihomo-fork/snell-v4.yaml) | Snell v4 |
| [snell-v5.yaml](examples/mihomo-fork/snell-v5.yaml) | Snell v5 的 TCP 传输 |
| [snell-v6.yaml](examples/mihomo-fork/snell-v6.yaml) | Snell v6 默认模式 |

1. 打开需要的示例文件，下载原始内容，修改参数后保存为 UTF-8 的 `.yaml` 文件。
2. 在 Solaris 的「订阅」页点击「导入文件」。
3. macOS 未连接时可能显示「连接后读取」；此时先按应用提示完成授权并连接，等待订阅读取完成。
4. 打开这份订阅的「更多」菜单，选择「使用它的规则」，确认出站模式为「规则」；在「代理」页确认 `PROXY` 组使用了目标节点。
5. 在应用中选择接管方式并连接，再通过 HTTP 测速或实际请求验证。

每份完整示例都包含 `proxies`、一个 `select` 策略组和 `MATCH,PROXY` 规则，便于先验证连通性。
这条规则会把匹配到的流量交给 `PROXY`，没有故障直连回退。需要自己的分流规则时，可以把节点加入现有配置，
或把该订阅加入节点池，在「我的方案」中使用；详见 [分流文档](routing.md)。

如果只复制下文的节点片段，将它放在现有 YAML 的 `proxies` 列表中，保持 `name` 唯一，再把名称加入所用策略组的
`proxies` 列表。远程订阅会按链接更新；需要自定义节点时，建议保留一份独立的本地订阅。

Solaris 的连接、混合端口和 TUN 受应用设置管理，`external-controller`、`secret` 等控制器字段会被清空。
本文的客户端出站示例不需要添加 `listeners`。入站配置还受宿主和局域网访问设置约束；本文不作为服务端部署说明。

## Naive

### HTTP/2：从最小配置开始

```yaml
proxies:
  - name: Naive-H2
    type: naive
    server: proxy.example.com
    port: 443
    username: 'CHANGE_ME_USERNAME'
    password: 'CHANGE_ME_PASSWORD'
    sni: proxy.example.com
    quic: false
```

`server` 填主机名或 IP，`port` 单独填写；不要把 `https://`、用户名、路径或端口拼进 `server`。
用户名不能含冒号 `:`。TLS 始终启用，证书需要与实际 TLS 主机名匹配；`sni` 省略时使用 `server`。
使用 IP 连接、证书签发给域名时，将 `sni` 填为证书对应的域名。

### HTTP/3/QUIC

在同一节点中设置：

```yaml
quic: true
# 可选；不填使用 Cronet 默认值
# quic-congestion-control: cubic
```

服务端必须支持对应的 Naive HTTP/3/QUIC 传输，网络也要允许到该服务端的 UDP。可选拥塞控制值是
`bbr`、`bbr2`、`cubic`、`reno`，这些选项仅在 `quic: true` 时有效。通过 `dialer-proxy` 串联其他节点时，
上游节点也需要支持 UDP。

**QUIC 只改变到代理服务器的传输方式，不会自动开启目标 UDP 流量转发。**

### 转发 UDP：需要 UoT

服务端支持 sing-box UoT 扩展时，在 Naive 节点中添加：

```yaml
udp-over-tcp: true
udp-over-tcp-version: 2
```

版本可选 `1` 或 `2`，默认 `2`；两端必须兼容。普通 Caddy/Naive 服务端不一定实现这个扩展。
Naive 的 UDP 能力由 `udp-over-tcp` 决定，单写 `udp: true` 会被拒绝；仅设置 `quic: true` 也不够。
通过 QUIC 承载 Naive 时，同样需要显式启用 UoT 才能转发目标 UDP 流量。

### 常用及高级参数

| 参数 | 含义与使用条件 |
| --- | --- |
| `username`、`password` | 服务端的认证信息；通常需要成对填写 |
| `sni` | TLS 主机名，省略时使用 `server` |
| `certificate` | 自定义可信 CA 的 PEM 正文或路径，用于验证服务端；这里不是客户端证书 |
| `ech-opts.enable` | 可选 ECH，需要服务端支持 |
| `ech-opts.config` | Base64 编码的 ECHConfigList；省略时查询 HTTPS DNS 记录 |
| `ech-opts.query-server-name` | 可选，指定 ECH DNS 查询名称 |
| `headers` | 可选的额外 HTTP 头映射；认证、padding、Host 等传输管理字段不能覆盖 |
| `ip-version` | `ipv4` / `ipv6` 可限制地址族；`ipv4-prefer` / `ipv6-prefer` 不会覆盖 Chromium 自己的地址族竞速策略 |
| `insecure-concurrency` | 非负整数，默认使用一条连接；大于 1 会改变流量特征，通常无需调整 |
| `stream-receive-window` | 字节数，`0` 使用 Cronet 默认值；HTTP/2 上限 `2147483647` |
| `quic-session-receive-window` | QUIC 会话接收窗口，字节数，仅在 QUIC 模式可用；QUIC 窗口须小于 `2^62` |

Naive 使用 Chromium TLS，不支持 `skip-cert-verify: true`，也不支持非空的 `client-fingerprint`、`fingerprint`、
`name-cert-verify`、`private-key`、`alpn`。从其他协议复制这些选项过来会报错。

自签名或私有 CA 场景，使用节点的 `certificate` 指定可信 CA；建议把 PEM 正文直接写进 YAML 的块字符串。
证书文件路径按内核配置目录解析，受安全路径规则限制，**不是相对于导入的 YAML 文件**。
Naive 不复用其他 Go 出站的全局证书池；更换 CA 后需要重新创建该出站，例如重载配置或重新连接。

## AnyTLS + REALITY

服务端必须同时使用 AnyTLS 协议和 REALITY。普通 AnyTLS TLS 端点或 VLESS + REALITY 端点都不能直接套用这份 AnyTLS 配置。

```yaml
proxies:
  - name: AnyTLS-REALITY
    type: anytls
    server: proxy.example.com
    port: 443
    password: 'CHANGE_ME_ANYTLS_PASSWORD'
    sni: cover.example.com
    client-fingerprint: chrome
    reality-opts:
      public-key: 'CHANGE_ME_REALITY_PUBLIC_KEY'
      short-id: '0123456789abcdef'
      # 默认已开启；需要旧服务端兼容时才显式设置 false
      # support-x25519mlkem768: false
```

| 参数 | 应当填写什么 |
| --- | --- |
| `password` | AnyTLS 服务端密码；REALITY 公钥不会替代这一层认证 |
| `sni` | 服务端允许的 REALITY server name |
| `client-fingerprint` | 必须是内核支持的有效指纹，示例使用 `chrome` |
| `reality-opts.public-key` | 服务端的 X25519 公钥：无填充的 Base64URL，解码后 32 字节；不要填私钥 |
| `reality-opts.short-id` | 服务端允许的 short ID：偶数位十六进制，最多 16 个字符；建议保留引号避免丢失前导零，空值也需服务端允许 |
| `udp` | 可选；需要 UDP 且服务端支持相应 UoT 时设为 `true` |

`REALITY`、`shadow-tls-opts`、`restls-opts`、`jls-opts` 只能启用一种。
AnyTLS + REALITY 还不能同时启用 ECH，也不能同时填写客户端证书 `certificate` / `private-key`。
基础配置连通前，先不要混入这些其他安全模式的参数。

## REALITY 的默认行为变化

这些变化属于共用的 REALITY 实现，也会影响使用它的 VLESS、VMess、Trojan 等配置。

- REALITY 握手中的客户端版本标识为 `26.10.29`。如果服务端限制了客户端版本范围，需要与服务端管理员核对该范围。
- 省略 `support-x25519mlkem768` 或设置为 `true` 时，保留所选指纹中已有的 X25519MLKEM768 混合密钥交换。
  这不会给原本没有该 key share 的指纹强行添加一个。
- 只有确认旧服务端不兼容时，才在已有的 `reality-opts` 内显式设置 `support-x25519mlkem768: false`。
  这个开关只影响 ClientHello 的密钥交换内容，握手版本标识仍为 `26.10.29`。

```yaml
reality-opts:
  public-key: 'CHANGE_ME_REALITY_PUBLIC_KEY'
  short-id: '0123456789abcdef'
  support-x25519mlkem768: false
```

布尔值使用 YAML 的 `true` / `false`。如果原配置已显式选择 `false`，Solaris 会保留这个选择。
连接失败时先核对地址、SNI、公钥、short ID、密码或 UUID、系统时间及服务端策略，再判断是否需要旧握手兼容开关。

## Snell v4、v5、v6

**务必显式填写 `version`。** 省略它仍按 v1 处理，不会自动协商到 v6。

v4 / v5 的最小节点示例：

```yaml
proxies:
  - name: Snell-v4
    type: snell
    server: proxy.example.com
    port: 44046
    psk: 'CHANGE_ME_SNELL_PSK'
    version: 4
    udp: false
    reuse: false
    # 多用户服务端需要时再填写
    # user-key: 'CHANGE_ME_USER_KEY'
```

使用 v5 服务端时改为 `version: 5`。这里的 v5 支持 TCP 传输和 UDP-over-TCP，不包含 Snell v5 QUIC proxy。

v6 节点示例：

```yaml
proxies:
  - name: Snell-v6
    type: snell
    server: proxy.example.com
    port: 44046
    psk: 'CHANGE_ME_SNELL_PSK'
    version: 6
    version6-mode: default
    udp: false
    reuse: false
    # user-key: 'CHANGE_ME_USER_KEY'
```

| 参数 | 规则 |
| --- | --- |
| `version` | 支持 `1`–`6`，默认 `1`；v4/v5/v6 使用 sing-snell |
| `psk` | 与服务端一致；v6 要求 12–255 字节 |
| `user-key` | v4/v5/v6 的可选用户密钥，与服务端对应；最多 255 字节；不替代 `psk` |
| `reuse` | v4/v5/v6 可选，默认 `false`；需要 TCP 连接复用时设为 `true`。v2 始终启用复用 |
| `udp` | 默认 `false`；v3–v6 可设为 `true`，同时需要服务端支持 UDP-over-TCP；v1/v2 不支持 |
| `version6-mode` | 仅 v6 可用，可选 `default`、`unshaped`、`unsafe-raw`，必须与服务端匹配 |
| `obfs-opts` | 可选，需与服务端一致；保留 `http`、`tls`、`shadow-tls`、`restls`、`jls` 的配置方式 |

先用默认 `version6-mode: default` 验证。`unshaped` 保留加密，但去掉默认的记录填充，需要服务端使用相同设置。
**`unsafe-raw` 不提供加密和 PSK 验证，不要把它当成常规加密代理配置。**

v4/v5 的 UDP 数据报受当前记录载荷窗口限制，过大的单个数据报会被拒绝；打开 `udp: true` 不会消除该限制。
已有 v1–v3 节点不需要为了升级内核而改版本。仅在服务端也已升级时切换客户端的 `version`。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| Naive 提示缺少 `with_naive` 或原生运行库 | 核对实际内核和构建。使用完整的 Solaris 安装包；开发构建按 [开发文档](development.md) 启用标签与匹配的原生库，不单独替换来历不同的 Cronet DLL |
| Naive 证书错误 | 检查域名、`sni`、系统时间和证书链；私有 CA 用节点 `certificate`，不要设置 `skip-cert-verify: true` |
| QUIC 能连接，但 UDP 应用不通 | QUIC 传输和目标 UDP 转发是两件事。Naive 需要 UoT，Snell 需要 `udp: true`，两者都需要服务端支持 |
| REALITY 公钥或 short ID 校验失败 | 替换占位符；公钥使用 32 字节的无填充 Base64URL；short ID 使用最多 16 位的偶数长度十六进制字符串 |
| AnyTLS 提示指纹或安全模式错误 | 使用有效 `client-fingerprint`；不要把 REALITY 与 ECH、客户端证书或其他安全模式混用 |
| Snell v6 或新字段不能使用 | 核对实际内核版本、显式 `version: 6`、PSK 长度和模式；服务端也要支持该版本 |
| 导入后仍使用旧节点 | 确认正在使用该订阅的规则，或已把它加入「我的方案」节点池，再检查出站模式、策略组选择和分流规则 |
| TCP 测速成功但实际请求失败 | TCP 测速只证明端口连接，不验证协议认证、REALITY 或 UDP；继续做 HTTP 测速和实际请求。Naive 请使用 HTTP 测速 |

示例测试检查 YAML、参数组合和 Solaris 配置准入；占位公钥会在测试中替换为测试值，不会使用真实节点凭据。
解析通过不代表你的服务端一定可达，实际连通性仍需用自己的服务端验证。

## 参数依据与进一步阅读

- [fork 的 Naive 说明](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/docs/naive.md)
- [Naive 参数与校验](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/adapter/outbound/naive_options.go)
- [AnyTLS 参数与模式互斥](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/adapter/outbound/anytls.go)
- [REALITY 参数](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/adapter/outbound/reality.go)与[握手实现](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/component/tls/reality.go)
- [Snell 参数](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/adapter/outbound/snell.go)与[v4–v6 适配](https://github.com/Ember-Moth/mihomo/blob/56c6fc1d5c271b207c8513a583a342b87a2230bc/adapter/outbound/snell_sing.go)
- [Solaris 的内核集成与约束](architecture.md)
