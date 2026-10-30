"""User-facing text for host and profile failures."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mihomo_desktop.host.base import HostError
from mihomo_desktop.i18n import QT_TRANSLATE_NOOP
from mihomo_desktop.models.profiles import ProfileError

_MESSAGES = {
    "not_implemented": QT_TRANSLATE_NOOP("Feedback", "该功能尚未实现"),
    "unsupported_method": QT_TRANSLATE_NOOP("Feedback", "当前平台不支持该操作"),
    "service_unavailable": QT_TRANSLATE_NOOP("Feedback", "未能连接到服务"),
    "connection_closed": QT_TRANSLATE_NOOP("Feedback", "与服务的连接已断开"),
    "timeout": QT_TRANSLATE_NOOP("Feedback", "服务没有响应"),
    "owned_by_other_user": QT_TRANSLATE_NOOP("Feedback", "隧道正由其他用户使用"),
    "invalid_config": QT_TRANSLATE_NOOP("Feedback", "配置无效"),
    "not_running": QT_TRANSLATE_NOOP("Feedback", "隧道未运行"),
    "already_running": QT_TRANSLATE_NOOP("Feedback", "隧道已在运行"),
    # A port or the TUN did not open, e.g. a mixed port another program holds.
    "apply_failed": QT_TRANSLATE_NOOP("Feedback", "配置未能生效"),
    "invalid_url": QT_TRANSLATE_NOOP("Feedback", "链接无效"),
    "download_failed": QT_TRANSLATE_NOOP("Feedback", "下载失败"),
    "http_error": QT_TRANSLATE_NOOP("Feedback", "下载失败"),
    "too_large": QT_TRANSLATE_NOOP("Feedback", "配置文件过大"),
    "not_text": QT_TRANSLATE_NOOP("Feedback", "配置不是 UTF-8 文本"),
    "empty_config": QT_TRANSLATE_NOOP("Feedback", "配置为空"),
    "file_unreadable": QT_TRANSLATE_NOOP("Feedback", "无法读取文件"),
    "config_missing": QT_TRANSLATE_NOOP("Feedback", "配置文件丢失，请重新导入"),
    "unknown_profile": QT_TRANSLATE_NOOP("Feedback", "配置不存在"),
    "no_profile": QT_TRANSLATE_NOOP("Feedback", "尚未导入配置"),
    "profile_in_use": QT_TRANSLATE_NOOP("Feedback", "正在使用的配置需要先断开才能删除"),
    "override_unreadable": QT_TRANSLATE_NOOP("Feedback", "无法读取覆写文件"),
    # The development service refuses both (docs/architecture.md §11).
    "tun_unsupported": QT_TRANSLATE_NOOP("Feedback", "开发模式不支持 TUN 模式"),
    "system_proxy_unsupported": QT_TRANSLATE_NOOP("Feedback", "开发模式不支持系统代理"),
    "tun_and_system_proxy_unsupported": QT_TRANSLATE_NOOP(
        "Feedback", "开发模式不支持 TUN 模式和系统代理"
    ),
    "settings_unwritable": QT_TRANSLATE_NOOP("Feedback", "无法保存设置"),
    "login_item_failed": QT_TRANSLATE_NOOP("Feedback", "无法更改登录时打开"),
    # The database could not be read or written (docs/ui.md §2).
    "storage_failed": QT_TRANSLATE_NOOP("Feedback", "无法读写本地数据"),
}
# Codes whose message (a parse error, an HTTP status) helps the user fix the problem.
_WITH_DETAIL = {
    "invalid_config",
    "apply_failed",
    "download_failed",
    "http_error",
    "file_unreadable",
    "override_unreadable",
    "settings_unwritable",
    "login_item_failed",
    "storage_failed",
}


def _failed(detail: str) -> str:
    return QCoreApplication.translate("Feedback", "操作失败：{detail}").format(detail=detail)


def failure_text(error: BaseException) -> str:
    if not isinstance(error, HostError | ProfileError):
        return _failed(str(error))
    message = _MESSAGES.get(error.code)
    if message is None:
        return _failed(error.message)
    text = QCoreApplication.translate("Feedback", message)
    if error.code in _WITH_DETAIL and error.message:
        detailed = QCoreApplication.translate("Feedback", "{text}：{detail}")
        return detailed.format(text=text, detail=error.message)
    return text
