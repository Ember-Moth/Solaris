# macOS 原生部分

这里将放 Xcode 工程，包含两个 target（docs/architecture.md §6）：

| target | 产物 | 职责 |
| --- | --- | --- |
| Manager | `<Product>Manager.dylib`，C ABI | 激活系统扩展、配置 `NETunnelProviderManager`、启停隧道、用 `sendProviderMessage` 转发控制消息 |
| PacketTunnel | `<bundle-id>.systemextension` | `NEPacketTunnelProvider`：设置隧道网络、取得 utun fd、运行内核；`handleAppMessage` 把消息交给 Go 分发器 |

Go 分发器由 `src/core/apple` 编成 C archive：

```bash
go -C src/core build -buildmode=c-archive -o ../../build/apple/libmihomo_desktop.a ./apple
```

生成的头文件声明两个函数：`mihomo_desktop_handle_message` 处理一条编码好的 Request 并返回 Response，
返回的缓冲区由调用方用 `mihomo_desktop_free` 释放。Swift 只转发字节，不解析消息。

尚未完成：Xcode 工程、签名与 entitlement、两种架构的切片合成 xcframework、实现 engine。
