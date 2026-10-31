// Solaris's installer surface. It never installs files, starts services or executes commands:
// NSIS owns those operations and reports their actual phase to this UI thread.
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <dwmapi.h>
#include <uxtheme.h>
#include <objidl.h>
#include <gdiplus.h>
#include "nsis_abi.h"

#include <algorithm>
#include <atomic>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "theme.h"

namespace {
HINSTANCE module = nullptr;
constexpr wchar_t windowClass[] = L"SolarisSetupWindow";
constexpr UINT updateMessage = WM_APP + 1;
constexpr UINT finishMessage = WM_APP + 2;
constexpr UINT captureMessage = WM_APP + 3;
constexpr UINT closeMessage = WM_APP + 4;
constexpr UINT scaleMessage = WM_APP + 5; // read-only preview layout probe
constexpr UINT_PTR progressTimer = 1;
constexpr int actionID = 1001, cancelID = 1002, detailsID = 1003, purgeID = 1004;
enum class Phase { Ready, Working, Done, Failed };

struct Update { std::wstring text; bool logOnly = false; };
struct Completion { bool success; std::wstring detail; };
struct CaptureRequest { std::wstring path; bool success = false; };

struct State {
  bool chinese = false, uninstall = false, upgrade = false, purgeDefault = false, preview = false;
  bool dark = false, highContrast = false, motion = true, details = false;
  bool privateFont = false, privateMono = false;
  std::wstring version, directory, assets, theme, log;
  std::vector<std::wstring> loadedFonts;
  Phase phase = Phase::Ready;
  Palette palette = kLight;
  UINT dpi = 96;
  std::atomic<HWND> window{nullptr};
  std::atomic<bool> started{false};
  HWND brand = nullptr, versionLabel = nullptr, headline = nullptr, body = nullptr;
  HWND label = nullptr, value = nullptr, note = nullptr, check = nullptr;
  HWND progress = nullptr, detailButton = nullptr, detailLabel = nullptr, detailEdit = nullptr;
  HWND primary = nullptr, secondary = nullptr, hovered = nullptr;
  HFONT titleFont = nullptr, bodyFont = nullptr, captionFont = nullptr, monoFont = nullptr;
  HFONT brandFont = nullptr, labelFont = nullptr, compactFont = nullptr;
  HICON icon = nullptr;
  HBRUSH background = nullptr, surface = nullptr;
  HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  HANDLE action = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  std::atomic<int> result{0}; // 0 cancel/close, 1 install/uninstall, 2 purge, 3 open app
  std::thread thread;

  ~State() {
    if (ready) CloseHandle(ready);
    if (action) CloseHandle(action);
  }
  int px(int value_) const { return MulDiv(value_, static_cast<int>(dpi), 96); }
  const wchar_t* tr(const wchar_t* zh, const wchar_t* en) const { return chinese ? zh : en; }
};
std::unique_ptr<State> state;

struct Call {
  int size;
  stack_t** stack;
  std::wstring pop() {
    if (!stack || !*stack) return {};
    stack_t* item = *stack;
    std::wstring value(item->text, wcsnlen_s(item->text, static_cast<size_t>(size)));
    *stack = item->next;
    GlobalFree(item);
    return value;
  }
  void push(const wchar_t* value) {
    if (!stack || size < 1) return;
    auto* item = static_cast<stack_t*>(GlobalAlloc(GPTR, sizeof(stack_t) + static_cast<size_t>(size) * sizeof(wchar_t)));
    if (!item) return;
    wcsncpy_s(item->text, static_cast<size_t>(size), value, _TRUNCATE);
    item->next = *stack;
    *stack = item;
  }
};

Gdiplus::Color color(COLORREF value) {
  return Gdiplus::Color(255, GetRValue(value), GetGValue(value), GetBValue(value));
}

void roundBox(HDC dc, RECT rect, COLORREF fill, COLORREF border, int radius, int stroke = 1) {
  Gdiplus::Graphics graphics(dc);
  graphics.SetSmoothingMode(Gdiplus::SmoothingModeAntiAlias);
  const float inset = static_cast<float>(stroke) / 2.0f;
  const float x = static_cast<float>(rect.left) + inset;
  const float y = static_cast<float>(rect.top) + inset;
  const float w = static_cast<float>(rect.right - rect.left) - static_cast<float>(stroke);
  const float h = static_cast<float>(rect.bottom - rect.top) - static_cast<float>(stroke);
  const float d = static_cast<float>(radius * 2);
  Gdiplus::GraphicsPath path;
  path.AddArc(x, y, d, d, 180.0f, 90.0f);
  path.AddArc(x + w - d, y, d, d, 270.0f, 90.0f);
  path.AddArc(x + w - d, y + h - d, d, d, 0.0f, 90.0f);
  path.AddArc(x, y + h - d, d, d, 90.0f, 90.0f);
  path.CloseFigure();
  Gdiplus::SolidBrush brush(color(fill));
  Gdiplus::Pen pen(color(border), static_cast<float>(stroke));
  graphics.FillPath(&brush, &path);
  graphics.DrawPath(&pen, &path);
}

void show(HWND control, bool visible) { ShowWindow(control, visible ? SW_SHOW : SW_HIDE); }
void text(HWND control, const std::wstring& value) { SetWindowTextW(control, value.c_str()); }

void applyTheme(State& s) {
  if (!s.window.load()) return;
  HIGHCONTRASTW hc{};
  hc.cbSize = sizeof(hc);
  s.highContrast = SystemParametersInfoW(SPI_GETHIGHCONTRAST, sizeof(hc), &hc, 0) && (hc.dwFlags & HCF_HIGHCONTRASTON);
  DWORD light = 1, bytes = sizeof(light);
  RegGetValueW(HKEY_CURRENT_USER, L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
               L"AppsUseLightTheme", RRF_RT_REG_DWORD, nullptr, &light, &bytes);
  s.dark = s.theme == L"dark" || (s.theme != L"light" && light == 0);
  s.palette = s.dark ? kDark : kLight;
  if (s.highContrast) {
    const COLORREF bg = GetSysColor(COLOR_WINDOW), fg = GetSysColor(COLOR_WINDOWTEXT);
    s.palette = {bg, bg, GetSysColor(COLOR_BTNFACE), fg, fg, fg, fg, fg,
                 GetSysColor(COLOR_GRAYTEXT), GetSysColor(COLOR_HIGHLIGHT),
                 GetSysColor(COLOR_HIGHLIGHT), GetSysColor(COLOR_HIGHLIGHT),
                 GetSysColor(COLOR_HIGHLIGHTTEXT), fg, GetSysColor(COLOR_BTNFACE)};
  }
  BOOL animation = TRUE;
  SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &animation, 0);
  s.motion = animation && !s.highContrast;
  if (s.background) DeleteObject(s.background);
  if (s.surface) DeleteObject(s.surface);
  s.background = CreateSolidBrush(s.palette.background);
  s.surface = CreateSolidBrush(s.palette.surface);
  const HWND window = s.window.load();
  const BOOL dark = s.dark && !s.highContrast;
  DwmSetWindowAttribute(window, DWMWA_USE_IMMERSIVE_DARK_MODE, &dark, sizeof(dark));
  if (s.phase == Phase::Working && s.motion && !s.details) SetTimer(window, progressTimer, 32, nullptr);
  else KillTimer(window, progressTimer);
  RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_ERASE);
}

HFONT font(State& s, int pixels, int weight, const wchar_t* family) {
  return CreateFontW(-s.px(pixels), 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                    OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                    DEFAULT_PITCH | FF_DONTCARE, family);
}

bool hasFont(const wchar_t* family) {
  LOGFONTW query{};
  query.lfCharSet = DEFAULT_CHARSET;
  wcscpy_s(query.lfFaceName, family);
  bool found = false;
  HDC dc = GetDC(nullptr);
  if (dc) {
    EnumFontFamiliesExW(dc, &query, [](const LOGFONTW*, const TEXTMETRICW*, DWORD, LPARAM data) -> int {
      *reinterpret_cast<bool*>(data) = true;
      return 0;
    }, reinterpret_cast<LPARAM>(&found), 0);
    ReleaseDC(nullptr, dc);
  }
  return found;
}

void makeFonts(State& s) {
  const wchar_t* latin = s.privateFont ? L"Inter" : L"Segoe UI";
  const wchar_t* family = latin;
  if (s.chinese) {
    // English/Server images may lack the optional Chinese UI fonts. Do not let
    // GDI choose an unrelated Latin serif face when the requested font is absent.
    family = hasFont(L"Microsoft YaHei UI") ? L"Microsoft YaHei UI"
           : hasFont(L"Microsoft YaHei") ? L"Microsoft YaHei" : L"Segoe UI";
  }
  const HFONT previous[] = {s.titleFont, s.bodyFont, s.captionFont, s.monoFont, s.brandFont, s.labelFont, s.compactFont};
  s.titleFont = font(s, kTitleSize, kTitleWeight, family);
  s.bodyFont = font(s, kBodySize, kBodyWeight, family);
  s.captionFont = font(s, kCaptionSize, kCaptionWeight, family);
  s.labelFont = font(s, kLabelSize, kLabelWeight, family);
  s.compactFont = font(s, kCompactSize, kCompactWeight, family);
  s.monoFont = font(s, kMonoSize, kMonoWeight, s.privateMono ? L"JetBrains Mono" : L"Consolas");
  s.brandFont = font(s, kTitleSize, kTitleWeight, latin);
  for (HWND control : {s.body, s.check})
    SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(s.bodyFont), FALSE);
  for (HWND control : {s.primary, s.secondary})
    SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(s.labelFont), FALSE);
  for (HWND control : {s.versionLabel, s.label, s.note, s.detailLabel})
    SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(s.captionFont), FALSE);
  SendMessageW(s.detailButton, WM_SETFONT, reinterpret_cast<WPARAM>(s.compactFont), FALSE);
  SendMessageW(s.brand, WM_SETFONT, reinterpret_cast<WPARAM>(s.brandFont), FALSE);
  SendMessageW(s.headline, WM_SETFONT, reinterpret_cast<WPARAM>(s.titleFont), FALSE);
  SendMessageW(s.value, WM_SETFONT, reinterpret_cast<WPARAM>(s.monoFont), FALSE);
  SendMessageW(s.detailEdit, WM_SETFONT, reinterpret_cast<WPARAM>(s.chinese ? s.captionFont : s.monoFont), FALSE);
  for (HFONT old : previous) if (old) DeleteObject(old);
  HICON oldIcon = s.icon;
  s.icon = static_cast<HICON>(LoadImageW(nullptr, (s.assets + L"\\solaris.ico").c_str(), IMAGE_ICON,
                                       s.px(40), s.px(40), LR_LOADFROMFILE));
  SendMessageW(s.window.load(), WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(s.icon));
  SendMessageW(s.window.load(), WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(s.icon));
  if (oldIcon) DestroyIcon(oldIcon);
}

void place(State& s, HWND control, int x, int y, int width, int height) {
  SetWindowPos(control, nullptr, s.px(x), s.px(y), s.px(width), s.px(height), SWP_NOZORDER | SWP_NOACTIVATE);
}

void layout(State& s) {
  place(s, s.brand, 76, 24, 380, 28);
  place(s, s.versionLabel, 76, 52, 480, 20);
  place(s, s.headline, 24, 104, 592, 28);
  place(s, s.body, 24, 144, 592, 44);
  place(s, s.label, 44, 216, 552, 20);
  place(s, s.value, 44, 244, 552, 24);
  place(s, s.note, 44, 280, 552, 20);
  place(s, s.check, 44, 220, 552, 28);
  place(s, s.progress, 44, 252, 552, 4);
  place(s, s.detailLabel, 44, 212, 552, 20);
  place(s, s.detailEdit, 44, 240, 552, 64);
  place(s, s.detailButton, 24, 328, 112, 32);
  place(s, s.primary, 364, 380, 148, kButtonHeight);
  place(s, s.secondary, 520, 380, 96, kButtonHeight);
}

void appendLog(State& s, const std::wstring& line) {
  if (line.empty()) return;
  if (!s.log.empty()) s.log += L"\r\n";
  s.log += line;
  if (s.log.size() > 32768) s.log.erase(0, s.log.size() - 32768);
  text(s.detailEdit, s.log);
  SendMessageW(s.detailEdit, EM_SETSEL, static_cast<WPARAM>(-1), static_cast<LPARAM>(-1));
  SendMessageW(s.detailEdit, EM_SCROLLCARET, 0, 0);
}

HWND defaultButton(State& s) {
  return s.uninstall && s.phase == Phase::Ready ? s.secondary : s.primary;
}

void renderState(State& s) {
  const bool ready = s.phase == Phase::Ready, busy = s.phase == Phase::Working;
  const bool done = s.phase == Phase::Done;
  const bool failed = s.phase == Phase::Failed;
  SendMessageW(s.value, WM_SETFONT, reinterpret_cast<WPARAM>((ready || done) && !s.uninstall ? s.monoFont : s.bodyFont), FALSE);
  SendMessageW(s.label, WM_SETFONT, reinterpret_cast<WPARAM>(busy ? s.bodyFont : s.captionFont), FALSE);
  const std::wstring version = std::wstring(s.tr(L"版本 ", L"Version ")) + s.version + L" · Windows x64";
  text(s.versionLabel, version);
  if (ready) {
    text(s.headline, s.uninstall ? s.tr(L"卸载 Solaris", L"Uninstall Solaris")
                    : s.upgrade ? s.tr(L"更新 Solaris", L"Update Solaris") : s.tr(L"安装 Solaris", L"Install Solaris"));
    text(s.body, s.uninstall ? s.tr(L"卸载将断开连接。默认保留你的订阅、设置和日志。", L"Uninstalling disconnects Solaris. Your profiles, settings and logs are kept by default.")
                : s.upgrade ? s.tr(L"更新期间会暂时断开连接，现有订阅和设置会保留。", L"The connection will pause during the update. Your profiles and settings will be kept.")
                : s.tr(L"安装应用与后台连接服务。关闭界面后，连接仍可继续运行。", L"Install the app and its background connection service. Connections can continue after you close the app."));
    text(s.label, s.tr(L"安装位置", L"Install location"));
    text(s.value, s.directory);
    text(s.note, s.uninstall ? s.tr(L"包含后台服务保存的所有用户配置。", L"Includes all users’ connection data saved by the service.")
                : s.tr(L"可在 Windows「设置 › 应用」中卸载。", L"You can uninstall from Windows Settings > Apps."));
    text(s.primary, s.uninstall ? s.tr(L"卸载", L"Uninstall")
                   : s.upgrade ? s.tr(L"更新 Solaris", L"Update Solaris") : s.tr(L"安装 Solaris", L"Install Solaris"));
    text(s.secondary, s.tr(L"取消", L"Cancel"));
  } else if (busy) {
    text(s.headline, s.uninstall ? s.tr(L"正在卸载…", L"Uninstalling…")
                    : s.upgrade ? s.tr(L"正在更新…", L"Updating…") : s.tr(L"正在安装…", L"Installing…"));
    text(s.body, s.tr(L"请稍候，当前操作完成后即可继续。", L"Please wait while the current operation finishes."));
    text(s.note, s.tr(L"可在下方查看本次操作的详情。", L"You can view the operation details below."));
    text(s.secondary, s.tr(L"关闭", L"Close"));
  } else {
    text(s.headline, failed ? (s.uninstall ? s.tr(L"未能完成卸载", L"Couldn’t uninstall Solaris") : s.tr(L"未能完成安装", L"Couldn’t install Solaris"))
                    : s.uninstall ? s.tr(L"Solaris 已卸载", L"Solaris was uninstalled")
                    : s.tr(L"Solaris 已准备就绪", L"Solaris is ready"));
    text(s.body, failed ? (s.uninstall ? s.tr(L"查看详情了解原因，然后重新运行卸载程序。", L"View the details, then run the uninstaller again.") : s.tr(L"查看详情了解原因，然后重新运行安装程序。", L"View the details, then run the installer again."))
                : s.uninstall ? s.tr(L"应用和后台连接服务已移除。", L"The app and its background connection service have been removed.")
                : s.tr(L"打开 Solaris，导入订阅或配置，开始连接。", L"Open Solaris, import a profile and connect."));
    text(s.label, failed ? s.tr(L"操作未完成", L"Operation incomplete") : s.tr(L"操作已完成", L"Operation complete"));
    text(s.value, failed ? s.tr(L"可展开详情查看错误信息", L"Open details to view the error")
                 : s.uninstall ? s.tr(L"感谢使用 Solaris", L"Thank you for using Solaris") : s.directory);
    text(s.note, failed ? s.tr(L"操作详情可用于定位问题。", L"Operation details can help identify the problem.")
                : s.uninstall ? s.tr(L"数据按卸载时的选择处理。", L"Data was handled according to your uninstall choice.")
                : s.tr(L"连接建立后，退出界面不会结束隧道。", L"Once connected, quitting the app keeps the tunnel running."));
    text(s.primary, done && !s.uninstall ? s.tr(L"打开 Solaris", L"Open Solaris") : s.tr(L"关闭", L"Close"));
    text(s.secondary, s.tr(L"关闭", L"Close"));
  }
  text(s.detailButton, s.details ? s.tr(L"收起详情", L"Hide details") : s.tr(L"查看详情", L"View details"));
  show(s.check, ready && s.uninstall);
  show(s.progress, busy && !s.details);
  show(s.label, !s.details && !(ready && s.uninstall));
  show(s.value, !s.details && !busy && !(ready && s.uninstall));
  show(s.note, !s.details);
  show(s.detailButton, !ready);
  show(s.detailLabel, s.details && !ready);
  show(s.detailEdit, s.details && !ready);
  show(s.primary, !busy);
  show(s.secondary, ready || busy || (done && !s.uninstall));
  EnableWindow(s.secondary, !busy);
  const HWND window = s.window.load();
  if (busy && s.motion && !s.details) SetTimer(window, progressTimer, 32, nullptr);
  else KillTimer(window, progressTimer);
  EnableMenuItem(GetSystemMenu(window, FALSE), SC_CLOSE, MF_BYCOMMAND | (busy ? MF_GRAYED : MF_ENABLED));
  DrawMenuBar(window);
  RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_ERASE);
}

void drawButton(State& s, DRAWITEMSTRUCT& item) {
  RECT rect = item.rcItem;
  const bool disabled = (item.itemState & ODS_DISABLED) != 0;
  const bool pressed = (item.itemState & ODS_SELECTED) != 0;
  const bool hover = s.hovered == item.hwndItem;
  const bool destructive = item.CtlID == actionID && s.uninstall && s.phase == Phase::Ready;
  const bool primary = item.hwndItem == defaultButton(s) && item.CtlID != detailsID;
  COLORREF fill = s.palette.surface, border = s.palette.border_button, ink = s.palette.text;
  if (primary) {
    fill = pressed ? s.palette.primary_pressed : hover ? s.palette.primary_hover : s.palette.primary;
    border = fill;
    ink = s.palette.on_primary;
  } else if (pressed || hover) fill = s.palette.surface_subtle;
  if (destructive) ink = s.palette.danger;
  if (disabled) { fill = s.palette.background; ink = s.palette.text_muted; }
  FillRect(item.hDC, &rect, s.background);
  roundBox(item.hDC, rect, fill, border, s.px(6));
  wchar_t label[256]{};
  GetWindowTextW(item.hwndItem, label, 256);
  SetBkMode(item.hDC, TRANSPARENT);
  SetTextColor(item.hDC, ink);
  const auto previous = SelectObject(item.hDC, item.CtlID == detailsID ? s.compactFont : s.labelFont);
  DrawTextW(item.hDC, label, -1, &rect, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
  SelectObject(item.hDC, previous);
  if ((item.itemState & ODS_FOCUS) && !(SendMessageW(s.window.load(), WM_QUERYUISTATE, 0, 0) & UISF_HIDEFOCUS)) {
    InflateRect(&rect, -s.px(3), -s.px(3));
    DrawFocusRect(item.hDC, &rect);
  }
}

LRESULT CALLBACK buttonProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR data) {
  auto& s = *reinterpret_cast<State*>(data);
  if (message == WM_MOUSEMOVE && s.hovered != window) {
    if (s.hovered) InvalidateRect(s.hovered, nullptr, FALSE);
    s.hovered = window;
    TRACKMOUSEEVENT tracking{sizeof(tracking), TME_LEAVE, window, 0};
    TrackMouseEvent(&tracking);
    InvalidateRect(window, nullptr, FALSE);
  } else if (message == WM_MOUSELEAVE) {
    if (s.hovered == window) s.hovered = nullptr;
    InvalidateRect(window, nullptr, FALSE);
  }
  return DefSubclassProc(window, message, wparam, lparam);
}

void drawCheckbox(State& s, HWND window, HDC dc) {
  RECT rect{};
  GetClientRect(window, &rect);
  FillRect(dc, &rect, s.surface);
  const int top = (rect.bottom - s.px(16)) / 2;
  RECT box{s.px(1), top, s.px(17), top + s.px(16)};
  const bool checked = SendMessageW(window, BM_GETCHECK, 0, 0) == BST_CHECKED;
  roundBox(dc, box, checked ? s.palette.primary : s.palette.surface,
           checked ? s.palette.primary : s.palette.border_control, s.px(4));
  if (checked) {
    Gdiplus::Graphics graphics(dc);
    graphics.SetSmoothingMode(Gdiplus::SmoothingModeAntiAlias);
    Gdiplus::Pen pen(color(s.palette.on_primary), static_cast<float>(s.px(2)));
    Gdiplus::Point points[] = {{s.px(4), top + s.px(8)}, {s.px(8), top + s.px(12)}, {s.px(14), top + s.px(4)}};
    graphics.DrawLines(&pen, points, 3);
  }
  wchar_t label[256]{};
  GetWindowTextW(window, label, 256);
  rect.left = s.px(26);
  const auto old = SelectObject(dc, s.bodyFont);
  SetBkMode(dc, TRANSPARENT);
  SetTextColor(dc, s.palette.text);
  DrawTextW(dc, label, -1, &rect, DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
  SelectObject(dc, old);
  if (GetFocus() == window && !(SendMessageW(s.window.load(), WM_QUERYUISTATE, 0, 0) & UISF_HIDEFOCUS)) DrawFocusRect(dc, &rect);
}

LRESULT CALLBACK checkboxProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR data) {
  auto& s = *reinterpret_cast<State*>(data);
  if (message == WM_ERASEBKGND) return 1;
  if (message == WM_PAINT) {
    PAINTSTRUCT paint{};
    HDC dc = BeginPaint(window, &paint);
    drawCheckbox(s, window, dc);
    EndPaint(window, &paint);
    return 0;
  }
  if (message == WM_PRINTCLIENT) { drawCheckbox(s, window, reinterpret_cast<HDC>(wparam)); return 0; }
  return DefSubclassProc(window, message, wparam, lparam);
}

LRESULT CALLBACK detailProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR) {
  const LRESULT result = DefSubclassProc(window, message, wparam, lparam);
  if (message == WM_SETFOCUS || message == WM_KILLFOCUS) {
    if (const HWND parent = GetParent(window)) InvalidateRect(parent, nullptr, FALSE);
  }
  return result;
}

void drawProgress(State& s, HWND window, HDC dc) {
  RECT rect{};
  GetClientRect(window, &rect);
  FillRect(dc, &rect, s.surface);
  const int height = rect.bottom;
  if (height < 1 || rect.right < height) return;
  // This is an indeterminate activity indicator, never an estimated percentage.
  // With reduced motion it becomes a steady accent line; the adjacent text still
  // names the actual installation phase.
  const COLORREF track = s.motion ? s.palette.control_track : s.palette.primary;
  roundBox(dc, rect, track, track, height / 2, 0);
  if (s.motion) {
    const int width = rect.right / 4;
    const int travel = rect.right - width;
    const int tick = static_cast<int>(GetTickCount64() % 2400);
    const int position = MulDiv(travel, tick <= 1200 ? tick : 2400 - tick, 1200);
    RECT segment{position, 0, position + width, height};
    roundBox(dc, segment, s.palette.primary, s.palette.primary, height / 2, 0);
  }
}

LRESULT CALLBACK progressProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR data) {
  auto& s = *reinterpret_cast<State*>(data);
  // The classic progress class otherwise reserves and paints an etched frame
  // outside WM_PAINT. Use the entire control rectangle for the rounded track.
  if (message == WM_NCCALCSIZE || message == WM_NCPAINT) return 0;
  if (message == WM_ERASEBKGND) return 1;
  if (message == WM_PAINT) {
    PAINTSTRUCT paint{};
    HDC dc = BeginPaint(window, &paint);
    drawProgress(s, window, dc);
    EndPaint(window, &paint);
    return 0;
  }
  if (message == WM_PRINTCLIENT) { drawProgress(s, window, reinterpret_cast<HDC>(wparam)); return 0; }
  return DefSubclassProc(window, message, wparam, lparam);
}

HWND control(State& s, const wchar_t* type, const wchar_t* caption, DWORD style, int id = 0) {
  HWND window = CreateWindowExW(0, type, caption, WS_CHILD | WS_VISIBLE | style, 0, 0, 0, 0,
                               s.window.load(), reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), module, nullptr);
  if (!window) throw std::runtime_error("create control");
  return window;
}

void makeControls(State& s) {
  s.brand = control(s, L"STATIC", L"Solaris", SS_NOPREFIX);
  s.versionLabel = control(s, L"STATIC", L"", SS_NOPREFIX);
  s.headline = control(s, L"STATIC", L"", SS_NOPREFIX);
  s.body = control(s, L"STATIC", L"", SS_NOPREFIX);
  s.label = control(s, L"STATIC", L"", SS_NOPREFIX);
  s.value = control(s, L"STATIC", L"", SS_NOPREFIX);
  s.note = control(s, L"STATIC", L"", SS_NOPREFIX);
  s.check = control(s, L"BUTTON", s.tr(L"同时清除订阅、设置和日志", L"Also delete profiles, settings and logs"),
                    BS_AUTOCHECKBOX | WS_TABSTOP, purgeID);
  SetWindowSubclass(s.check, checkboxProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  SendMessageW(s.check, BM_SETCHECK, s.purgeDefault ? BST_CHECKED : BST_UNCHECKED, 0);
  s.progress = control(s, PROGRESS_CLASSW, s.tr(L"操作进度", L"Operation progress"), PBS_MARQUEE | PBS_SMOOTH);
  SetWindowTheme(s.progress, L"", L"");
  SetWindowSubclass(s.progress, progressProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  s.detailButton = control(s, L"BUTTON", L"", BS_OWNERDRAW | WS_TABSTOP, detailsID);
  s.detailLabel = control(s, L"STATIC", s.tr(L"操作详情", L"Operation details"), SS_NOPREFIX);
  s.detailEdit = control(s, L"EDIT", L"", ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | WS_VSCROLL | WS_TABSTOP);
  SetWindowSubclass(s.detailEdit, detailProcedure, 1, 0);
  SendMessageW(s.detailEdit, EM_LIMITTEXT, 32768, 0);
  s.primary = control(s, L"BUTTON", L"", BS_OWNERDRAW | WS_TABSTOP, actionID);
  s.secondary = control(s, L"BUTTON", L"", BS_OWNERDRAW | WS_TABSTOP, cancelID);
  for (HWND button : {s.primary, s.secondary, s.detailButton})
    SetWindowSubclass(button, buttonProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  makeFonts(s);
  layout(s);
  applyTheme(s);
  renderState(s);
}

bool screenshot(HWND window, const std::wstring& path) {
  RECT rect{};
  GetWindowRect(window, &rect);
  const int width = rect.right - rect.left, height = rect.bottom - rect.top;
  HDC screen = GetDC(window), memory = CreateCompatibleDC(screen);
  HBITMAP bitmap = CreateCompatibleBitmap(screen, width, height);
  const auto old = SelectObject(memory, bitmap);
  const BOOL captured = PrintWindow(window, memory, 2); // PW_RENDERFULLCONTENT
  SelectObject(memory, old);
  bool saved = false;
  if (captured) {
    UINT count = 0, size = 0;
    Gdiplus::GetImageEncodersSize(&count, &size);
    std::vector<BYTE> bytes(size);
    auto* encoders = reinterpret_cast<Gdiplus::ImageCodecInfo*>(bytes.data());
    if (Gdiplus::GetImageEncoders(count, size, encoders) == Gdiplus::Ok) {
      Gdiplus::Bitmap image(bitmap, nullptr);
      for (UINT i = 0; i < count; ++i) {
        if (wcscmp(encoders[i].MimeType, L"image/png") == 0) {
          saved = image.Save(path.c_str(), &encoders[i].Clsid, nullptr) == Gdiplus::Ok;
          break;
        }
      }
    }
  }
  DeleteObject(bitmap);
  DeleteDC(memory);
  ReleaseDC(window, screen);
  return saved;
}

void drawSurface(State& s, HWND window, HDC dc) {
  RECT rect{};
  GetClientRect(window, &rect);
  FillRect(dc, &rect, s.background);
  RECT card{s.px(24), s.px(200), s.px(616), s.px(316)};
  const bool detailFocus = GetFocus() == s.detailEdit;
  roundBox(dc, card, s.palette.surface, detailFocus ? s.palette.primary : s.palette.border,
           s.px(8), detailFocus ? s.px(2) : 1);
  if (s.icon) DrawIconEx(dc, s.px(24), s.px(28), s.icon, s.px(40), s.px(40), 0, nullptr, DI_NORMAL);
  HPEN pen = CreatePen(PS_SOLID, 1, s.palette.border);
  const auto old = SelectObject(dc, pen);
  MoveToEx(dc, s.px(24), s.px(368), nullptr);
  LineTo(dc, s.px(616), s.px(368));
  SelectObject(dc, old);
  DeleteObject(pen);
}

LRESULT CALLBACK windowProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
  auto* pointer = reinterpret_cast<State*>(GetWindowLongPtrW(window, GWLP_USERDATA));
  if (message == WM_NCCREATE) {
    pointer = static_cast<State*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
    SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(pointer));
    pointer->window.store(window);
  }
  if (!pointer) return DefWindowProcW(window, message, wparam, lparam);
  auto& s = *pointer;
  switch (message) {
    case WM_CREATE:
      try { makeControls(s); return 0; } catch (...) { return -1; }
    case WM_ERASEBKGND: return 1;
    case WM_PAINT: {
      PAINTSTRUCT paint{};
      HDC dc = BeginPaint(window, &paint);
      drawSurface(s, window, dc);
      EndPaint(window, &paint);
      return 0;
    }
    case WM_PRINTCLIENT: drawSurface(s, window, reinterpret_cast<HDC>(wparam)); return 0;
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT:
    case WM_CTLCOLORBTN: {
      HDC dc = reinterpret_cast<HDC>(wparam);
      HWND child = reinterpret_cast<HWND>(lparam);
      const bool inCard = child == s.label || child == s.value || child == s.note || child == s.check || child == s.detailLabel || child == s.detailEdit;
      const bool muted = child == s.versionLabel || child == s.note || child == s.detailLabel || (child == s.label && s.phase != Phase::Working);
      SetTextColor(dc, muted ? s.palette.text_muted : s.palette.text);
      SetBkColor(dc, inCard ? s.palette.surface : s.palette.background);
      return reinterpret_cast<LRESULT>(inCard ? s.surface : s.background);
    }
    case WM_DRAWITEM:
      if (wparam == actionID || wparam == cancelID || wparam == detailsID) {
        drawButton(s, *reinterpret_cast<DRAWITEMSTRUCT*>(lparam));
        return TRUE;
      }
      break;
    case WM_COMMAND: {
      const int id = LOWORD(wparam);
      if (id == detailsID && HIWORD(wparam) == BN_CLICKED) {
        s.details = !s.details;
        renderState(s);
      } else if (id == actionID && HIWORD(wparam) == BN_CLICKED) {
        if (s.phase == Phase::Ready) {
          s.result.store(s.uninstall && SendMessageW(s.check, BM_GETCHECK, 0, 0) == BST_CHECKED ? 2 : 1);
          s.phase = Phase::Working;
          text(s.label, s.tr(L"正在准备…", L"Preparing…"));
          renderState(s);
          SetFocus(s.detailButton);
          SetEvent(s.action);
        } else if (s.phase != Phase::Working) {
          s.result.store(s.phase == Phase::Done && !s.uninstall ? 3 : 0);
          DestroyWindow(window);
        }
      } else if (id == cancelID && HIWORD(wparam) == BN_CLICKED) {
        SendMessageW(window, WM_CLOSE, 0, 0);
      }
      return 0;
    }
    case WM_CLOSE:
      if (s.phase != Phase::Working) { s.result.store(0); DestroyWindow(window); }
      return 0;
    case WM_DESTROY:
      s.window.store(nullptr);
      SetEvent(s.action);
      PostQuitMessage(0);
      return 0;
    case WM_DPICHANGED: {
      s.dpi = HIWORD(wparam);
      const auto* suggested = reinterpret_cast<RECT*>(lparam);
      SetWindowPos(window, nullptr, suggested->left, suggested->top,
                   suggested->right - suggested->left, suggested->bottom - suggested->top,
                   SWP_NOZORDER | SWP_NOACTIVATE);
      makeFonts(s);
      layout(s);
      renderState(s);
      return 0;
    }
    case WM_THEMECHANGED:
    case WM_SETTINGCHANGE: applyTheme(s); return 0;
    case WM_TIMER:
      if (wparam == progressTimer && s.phase == Phase::Working) InvalidateRect(s.progress, nullptr, FALSE);
      return 0;
    case updateMessage: {
      std::unique_ptr<Update> update(reinterpret_cast<Update*>(lparam));
      appendLog(s, update->text);
      if (!update->logOnly) text(s.label, update->text);
      return 0;
    }
    case finishMessage: {
      std::unique_ptr<Completion> completion(reinterpret_cast<Completion*>(lparam));
      s.phase = completion->success ? Phase::Done : Phase::Failed;
      appendLog(s, completion->detail);
      s.details = false;
      renderState(s);
      SetFocus(defaultButton(s));
      return 0;
    }
    case captureMessage: {
      auto& request = *reinterpret_cast<CaptureRequest*>(lparam);
      request.success = screenshot(window, request.path);
      return 0;
    }
    case closeMessage: DestroyWindow(window); return 0;
    case scaleMessage: {
      if (!s.preview || wparam < 96 || wparam > 384) return 0;
      RECT outer{}, client{};
      GetWindowRect(window, &outer);
      GetClientRect(window, &client);
      const int frameWidth = outer.right - outer.left - client.right;
      const int frameHeight = outer.bottom - outer.top - client.bottom;
      s.dpi = static_cast<UINT>(wparam);
      SetWindowPos(window, nullptr, 0, 0, s.px(kWidth) + frameWidth, s.px(kHeight) + frameHeight,
                   SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
      makeFonts(s);
      layout(s);
      renderState(s);
      return 0;
    }
  }
  return DefWindowProcW(window, message, wparam, lparam);
}

void run(State& s) {
  ULONG_PTR gdiplusToken = 0;
  Gdiplus::GdiplusStartupInput input;
  try {
    using SetDpiContext = HANDLE (WINAPI*)(HANDLE);
    auto setDpi = reinterpret_cast<SetDpiContext>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetThreadDpiAwarenessContext"));
    if (setDpi) setDpi(reinterpret_cast<HANDLE>(static_cast<INT_PTR>(-4))); // per-monitor v2
    if (Gdiplus::GdiplusStartup(&gdiplusToken, &input, nullptr) != Gdiplus::Ok) throw std::runtime_error("GDI+");
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_STANDARD_CLASSES | ICC_PROGRESS_CLASS};
    InitCommonControlsEx(&controls);
    for (const wchar_t* name : {L"Inter-Regular.ttf", L"Inter-Medium.ttf", L"Inter-SemiBold.ttf", L"JetBrainsMono-Regular.ttf"}) {
      const std::wstring path = s.assets + L"\\" + name;
      if (AddFontResourceExW(path.c_str(), FR_PRIVATE, nullptr)) {
        s.loadedFonts.push_back(path);
        if (name[0] == L'I') s.privateFont = true; else s.privateMono = true;
      }
    }
    using GetDpi = UINT (WINAPI*)();
    auto getDpi = reinterpret_cast<GetDpi>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "GetDpiForSystem"));
    if (getDpi) s.dpi = getDpi();
    WNDCLASSEXW cls{};
    cls.cbSize = sizeof(cls);
    cls.hInstance = module;
    cls.lpfnWndProc = windowProcedure;
    cls.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    cls.lpszClassName = windowClass;
    RegisterClassExW(&cls);
    const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN;
    RECT size{0, 0, s.px(kWidth), s.px(kHeight)};
    using AdjustRect = BOOL (WINAPI*)(LPRECT, DWORD, BOOL, DWORD, UINT);
    auto adjust = reinterpret_cast<AdjustRect>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "AdjustWindowRectExForDpi"));
    if (adjust) adjust(&size, style, FALSE, 0, s.dpi); else AdjustWindowRectEx(&size, style, FALSE, 0);
    RECT work{};
    SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0);
    const int width = size.right - size.left, height = size.bottom - size.top;
    HWND window = CreateWindowExW(WS_EX_CONTROLPARENT, windowClass, s.tr(L"Solaris 安装程序", L"Solaris setup"), style,
                                  work.left + (work.right - work.left - width) / 2,
                                  work.top + (work.bottom - work.top - height) / 2,
                                  width, height, nullptr, nullptr, module, &s);
    if (!window) throw std::runtime_error("installer window");
    SendMessageW(window, WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(s.icon));
    SendMessageW(window, WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(s.icon));
    s.started.store(true);
    ShowWindow(window, SW_SHOWNORMAL);
    UpdateWindow(window);
    SetForegroundWindow(window);
    SetFocus(defaultButton(s));
    SendMessageW(window, WM_CHANGEUISTATE, MAKEWPARAM(UIS_SET, UISF_HIDEFOCUS), 0);
    SetEvent(s.ready);
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
      if (message.message == WM_KEYDOWN) {
        if (message.wParam == VK_TAB) {
          SendMessageW(window, WM_CHANGEUISTATE, MAKEWPARAM(UIS_CLEAR, UISF_HIDEFOCUS), 0);
          HWND next = GetNextDlgTabItem(window, GetFocus(), GetKeyState(VK_SHIFT) < 0);
          if (next) SetFocus(next);
          continue;
        }
        if (message.wParam == VK_ESCAPE) { SendMessageW(window, WM_CLOSE, 0, 0); continue; }
        if (message.wParam == VK_RETURN && s.phase != Phase::Working) {
          HWND focus = GetFocus();
          HWND button = focus == s.primary || focus == s.secondary || focus == s.detailButton ? focus : defaultButton(s);
          SendMessageW(button, BM_CLICK, 0, 0);
          continue;
        }
      }
      TranslateMessage(&message);
      DispatchMessageW(&message);
    }
  } catch (...) {
    if (s.window.load()) DestroyWindow(s.window.load());
    SetEvent(s.ready);
    SetEvent(s.action);
  }
  for (HFONT f : {s.titleFont, s.bodyFont, s.captionFont, s.monoFont, s.brandFont, s.labelFont, s.compactFont}) if (f) DeleteObject(f);
  if (s.icon) DestroyIcon(s.icon);
  if (s.background) DeleteObject(s.background);
  if (s.surface) DeleteObject(s.surface);
  for (const auto& path : s.loadedFonts) RemoveFontResourceExW(path.c_str(), FR_PRIVATE, nullptr);
  if (gdiplusToken) Gdiplus::GdiplusShutdown(gdiplusToken);
}

void closeUI() {
  if (!state) return;
  if (const HWND window = state->window.load()) PostMessageW(window, closeMessage, 0, 0);
  if (state->thread.joinable()) state->thread.join();
  state.reset();
}

UINT_PTR __cdecl pluginCallback(NSPIM message) {
  if (message == NSPIM_UNLOAD) { closeUI(); UnregisterClassW(windowClass, module); }
  return 0;
}

void postUpdate(const std::wstring& value, bool logOnly) {
  if (!state || !state->window.load()) return;
  auto update = std::make_unique<Update>();
  update->text = value;
  update->logOnly = logOnly;
  if (PostMessageW(state->window.load(), updateMessage, 0, reinterpret_cast<LPARAM>(update.get()))) update.release();
}
} // namespace

extern "C" void __cdecl Begin(HWND, int size, wchar_t*, stack_t** stack, extra_parameters* extra) {
  Call call{size, stack};
  closeUI();
  try {
    auto mode = call.pop();
    const auto language = call.pop(), version = call.pop();
    const auto directory = call.pop(), assets = call.pop(), theme = call.pop();
    state = std::make_unique<State>();
    if (mode.rfind(L"preview:", 0) == 0) { state->preview = true; mode.erase(0, 8); }
    state->uninstall = mode == L"uninstall" || mode == L"uninstall-purge";
    state->purgeDefault = mode == L"uninstall-purge";
    state->upgrade = mode == L"upgrade";
    state->chinese = language == L"2052";
    state->version = version;
    state->directory = directory;
    state->assets = assets;
    state->theme = theme;
    if (!state->ready || !state->action) throw std::runtime_error("installer events");
    if (extra->RegisterPluginCallback(module, pluginCallback) < 0) throw std::runtime_error("plugin lifetime");
    state->thread = std::thread([] { run(*state); });
    WaitForSingleObject(state->ready, INFINITE);
    if (!state->started.load()) { closeUI(); call.push(L"error"); return; }
    WaitForSingleObject(state->action, INFINITE);
    const int result = state->result.load();
    if (result == 1 || result == 2) call.push(result == 2 ? L"purge" : L"proceed");
    else { closeUI(); call.push(L"cancel"); }
  } catch (...) { closeUI(); call.push(L"error"); }
}

extern "C" void __cdecl Status(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack}; postUpdate(call.pop(), false);
}
extern "C" void __cdecl Log(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack}; postUpdate(call.pop(), true);
}
extern "C" void __cdecl Finish(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack};
  const auto result = call.pop(), detail = call.pop();
  if (!state || !state->window.load()) { call.push(L"close"); return; }
  ResetEvent(state->action);
  auto completion = std::make_unique<Completion>();
  completion->success = result == L"success";
  completion->detail = detail;
  if (!PostMessageW(state->window.load(), finishMessage, 0, reinterpret_cast<LPARAM>(completion.get()))) {
    closeUI(); call.push(L"close"); return;
  }
  completion.release();
  WaitForSingleObject(state->action, INFINITE);
  const bool open = state->result.load() == 3;
  closeUI();
  call.push(open ? L"open" : L"close");
}
extern "C" void __cdecl Capture(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack};
  CaptureRequest request{call.pop(), false};
  if (state && state->window.load()) {
    // Synchronous dispatch keeps the request alive until the capture is finished.
    SendMessageW(state->window.load(), captureMessage, 0, reinterpret_cast<LPARAM>(&request));
  }
  call.push(request.success ? L"ok" : L"error");
}
extern "C" void __cdecl Close(HWND, int, wchar_t*, stack_t**, extra_parameters*) { closeUI(); }

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) module = instance;
  return TRUE;
}
