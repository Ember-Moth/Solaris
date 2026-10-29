"""User-facing text for host failures."""

from __future__ import annotations

from mihomo_desktop.host.base import HostError

_MESSAGES = {
    "not_implemented": "该功能尚未实现",
    "unsupported_method": "当前平台不支持该操作",
    "service_unavailable": "未能连接到服务",
    "connection_closed": "与服务的连接已断开",
    "timeout": "服务没有响应",
    "owned_by_other_user": "隧道正由其他用户使用",
    "invalid_config": "配置无效",
    "not_running": "隧道未运行",
    "already_running": "隧道已在运行",
}


def failure_text(error: BaseException) -> str:
    if isinstance(error, HostError):
        return _MESSAGES.get(error.code, f"操作失败：{error.message}")
    return f"操作失败：{error}"
