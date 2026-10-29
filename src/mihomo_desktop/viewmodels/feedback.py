"""User-facing text for host and profile failures."""

from __future__ import annotations

from mihomo_desktop.host.base import HostError
from mihomo_desktop.models.profiles import ProfileError

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
    "invalid_url": "链接无效",
    "download_failed": "下载失败",
    "http_error": "下载失败",
    "too_large": "配置文件过大",
    "not_text": "配置不是 UTF-8 文本",
    "empty_config": "配置为空",
    "file_unreadable": "无法读取文件",
    "config_missing": "配置文件丢失，请重新导入",
    "unknown_profile": "配置不存在",
    "no_profile": "尚未导入配置",
    "profile_in_use": "正在使用的配置需要先断开才能删除",
}
# Codes whose message (a parse error, an HTTP status) helps the user fix the problem.
_WITH_DETAIL = {"invalid_config", "download_failed", "http_error", "file_unreadable"}


def failure_text(error: BaseException) -> str:
    if isinstance(error, HostError | ProfileError):
        text = _MESSAGES.get(error.code)
        if text is None:
            return f"操作失败：{error.message}"
        return f"{text}：{error.message}" if error.code in _WITH_DETAIL and error.message else text
    return f"操作失败：{error}"
