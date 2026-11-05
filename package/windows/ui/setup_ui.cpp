// Solaris's installer surface. It never installs files, starts services or executes commands:
// NSIS owns those operations and reports which step it is on to this UI thread.
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <dwmapi.h>
#include <uxtheme.h>
#include <objidl.h>
#include <gdiplus.h>
#include <shobjidl.h>
#include <wrl/client.h>
#include "nsis_abi.h"

#include <algorithm>
#include <atomic>
#include <initializer_list>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
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
constexpr UINT stepMessage = WM_APP + 6;
constexpr UINT_PTR spinnerTimer = 1;
constexpr int actionID = 1001, cancelID = 1002, detailsID = 1003, purgeID = 1004;
constexpr int agreementID = 1005, pathID = 1006, browseID = 1007, licenseID = 1008;
constexpr int licenseTextID = 1009, subtitleID = 1010, firstStepID = 1011, logID = 1015;
constexpr int stepLimit = 4;
// 96-DPI layout; the client size, margin, radii and button height come from DESIGN.md.
constexpr int footerHeight = 80, buttonWidth = 112, browseWidth = 88, iconSize = 48;
constexpr int bodyTop = 84, rowPitch = 32, rowHeight = 28, checkRow = 22;
enum class Phase { Ready, Working, Done, Failed };
enum class Mode { Install, Upgrade, Uninstall };
enum class Mark { Pending, Current, Done, Failed };

struct Completion { bool success; std::wstring detail; };
struct CaptureRequest { std::wstring path; bool success = false; };

struct State {
  Mode mode = Mode::Install;
  bool chinese = false, purgeDefault = false, preview = false, purged = false;
  bool dark = false, highContrast = false, motion = true, showDetails = false;
  bool privateFont = false, privateMono = false, licenseView = false;
  std::wstring version, installed, architecture, directory, assets, theme, log, license, failure;
  std::wstring agreementLabel, linkLabel;
  std::vector<std::wstring> loadedFonts, stepLabels;
  Phase phase = Phase::Ready;
  int step = 0;
  Palette palette = kLight;
  UINT dpi = 96;
  RECT field{}, box{};
  std::atomic<HWND> window{nullptr};
  std::atomic<bool> started{false};
  HWND title = nullptr, subtitle = nullptr, body = nullptr, pathLabel = nullptr, pathEdit = nullptr;
  HWND browse = nullptr, locationKey = nullptr, locationValue = nullptr, versionKey = nullptr;
  HWND versionValue = nullptr, purge = nullptr, purgeNote = nullptr, logLabel = nullptr, logEdit = nullptr;
  HWND licenseNote = nullptr, licenseTitle = nullptr, licenseEdit = nullptr, agreement = nullptr;
  HWND link = nullptr, detailButton = nullptr, primary = nullptr, secondary = nullptr, hovered = nullptr;
  HWND steps[stepLimit]{};
  HFONT titleFont = nullptr, bodyFont = nullptr, strongFont = nullptr, captionFont = nullptr;
  HFONT sectionFont = nullptr, labelFont = nullptr, monoFont = nullptr;
  HICON icon = nullptr, bigIcon = nullptr, smallIcon = nullptr;
  HBRUSH background = nullptr, surface = nullptr, subtle = nullptr;
  HANDLE ready = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  HANDLE action = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  std::atomic<int> result{0}; // 0 cancel/close, 1 install/uninstall, 2 purge, 3 open app
  std::thread thread;

  ~State() {
    if (ready) CloseHandle(ready);
    if (action) CloseHandle(action);
  }
  int px(int value_) const { return MulDiv(value_, static_cast<int>(dpi), 96); }
  float scale() const { return static_cast<float>(dpi) / 96.0f; }
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

// DESIGN.md's disabled opacity, flattened onto the colour underneath.
COLORREF mix(COLORREF top, COLORREF base, float alpha) {
  auto channel = [alpha](BYTE a, BYTE b) {
    return static_cast<BYTE>(static_cast<float>(a) * alpha + static_cast<float>(b) * (1.0f - alpha) + 0.5f);
  };
  return RGB(channel(GetRValue(top), GetRValue(base)), channel(GetGValue(top), GetGValue(base)),
             channel(GetBValue(top), GetBValue(base)));
}

// Coordinates follow SVG: pixel centres at .5, as in the prototype's icon geometry.
void smooth(Gdiplus::Graphics& graphics) {
  graphics.SetSmoothingMode(Gdiplus::SmoothingModeAntiAlias);
  graphics.SetPixelOffsetMode(Gdiplus::PixelOffsetModeHalf);
}

void roundBox(HDC dc, RECT rect, COLORREF paint, COLORREF border, float radius, float stroke = 1.0f) {
  Gdiplus::Graphics graphics(dc);
  smooth(graphics);
  const float inset = stroke / 2.0f;
  const float x = static_cast<float>(rect.left) + inset;
  const float y = static_cast<float>(rect.top) + inset;
  const float w = static_cast<float>(rect.right - rect.left) - stroke;
  const float h = static_cast<float>(rect.bottom - rect.top) - stroke;
  const float d = std::max(1.0f, radius * 2.0f - stroke);
  Gdiplus::GraphicsPath path;
  path.AddArc(x, y, d, d, 180.0f, 90.0f);
  path.AddArc(x + w - d, y, d, d, 270.0f, 90.0f);
  path.AddArc(x + w - d, y + h - d, d, d, 0.0f, 90.0f);
  path.AddArc(x, y + h - d, d, d, 90.0f, 90.0f);
  path.CloseFigure();
  Gdiplus::SolidBrush brush(color(paint));
  graphics.FillPath(&brush, &path);
  if (stroke > 0.0f) {
    Gdiplus::Pen pen(color(border), stroke);
    graphics.DrawPath(&pen, &path);
  }
}

void disc(Gdiplus::Graphics& graphics, float x, float y, float size, COLORREF paint) {
  Gdiplus::SolidBrush brush(color(paint));
  graphics.FillEllipse(&brush, x, y, size, size);
}

// A stroked polyline in a unit box at (x, y), scaled by f: the prototype's glyph paths.
void glyph(Gdiplus::Graphics& graphics, float x, float y, float f, COLORREF ink, float width,
           std::initializer_list<Gdiplus::PointF> points) {
  std::vector<Gdiplus::PointF> scaled;
  for (const auto& point : points) scaled.emplace_back(x + point.X * f, y + point.Y * f);
  Gdiplus::Pen pen(color(ink), width * f);
  pen.SetLineCap(Gdiplus::LineCapRound, Gdiplus::LineCapRound, Gdiplus::DashCapRound);
  pen.SetLineJoin(Gdiplus::LineJoinRound);
  graphics.DrawLines(&pen, scaled.data(), static_cast<INT>(scaled.size()));
}

template <typename Paint> void buffered(HDC target, const RECT& rect, Paint paint) {
  const int width = rect.right - rect.left, height = rect.bottom - rect.top;
  if (width <= 0 || height <= 0) return;
  HDC memory = CreateCompatibleDC(target);
  HBITMAP bitmap = CreateCompatibleBitmap(target, rect.right, rect.bottom);
  const auto old = SelectObject(memory, bitmap);
  paint(memory);
  BitBlt(target, rect.left, rect.top, width, height, memory, rect.left, rect.top, SRCCOPY);
  SelectObject(memory, old);
  DeleteObject(bitmap);
  DeleteDC(memory);
}

void show(HWND control, bool visible) { ShowWindow(control, visible ? SW_SHOW : SW_HIDE); }
void text(HWND control, const std::wstring& value) { SetWindowTextW(control, value.c_str()); }
bool checked(HWND control) { return SendMessageW(control, BM_GETCHECK, 0, 0) == BST_CHECKED; }
// IsWindowVisible is false for every control until the window itself is shown.
bool shown(HWND control) { return (GetWindowLongPtrW(control, GWL_STYLE) & WS_VISIBLE) != 0; }
bool focusHidden(State& s) { return (SendMessageW(s.window.load(), WM_QUERYUISTATE, 0, 0) & UISF_HIDEFOCUS) != 0; }

void fill(HDC dc, const RECT& rect, COLORREF value) {
  HBRUSH brush = CreateSolidBrush(value);
  FillRect(dc, &rect, brush);
  DeleteObject(brush);
}

SIZE measure(State& s, HFONT font, const std::wstring& value) {
  SIZE size{};
  HDC dc = GetDC(s.window.load());
  const auto old = SelectObject(dc, font);
  GetTextExtentPoint32W(dc, value.c_str(), static_cast<int>(value.size()), &size);
  SelectObject(dc, old);
  ReleaseDC(s.window.load(), dc);
  return size;
}

int lineHeight(State& s, HFONT font) {
  TEXTMETRICW metrics{};
  HDC dc = GetDC(s.window.load());
  const auto old = SelectObject(dc, font);
  GetTextMetricsW(dc, &metrics);
  SelectObject(dc, old);
  ReleaseDC(s.window.load(), dc);
  return metrics.tmHeight;
}

int wrappedHeight(State& s, HFONT font, const std::wstring& value, int width) {
  RECT rect{0, 0, width, 0};
  HDC dc = GetDC(s.window.load());
  const auto old = SelectObject(dc, font);
  DrawTextW(dc, value.c_str(), -1, &rect, DT_CALCRECT | DT_WORDBREAK | DT_EDITCONTROL | DT_NOPREFIX);
  SelectObject(dc, old);
  ReleaseDC(s.window.load(), dc);
  return rect.bottom;
}

// ---- copy -----------------------------------------------------------------------------

std::wstring headline(const State& s) {
  const bool install = s.mode == Mode::Install, upgrade = s.mode == Mode::Upgrade;
  switch (s.phase) {
    case Phase::Ready:
      return install ? s.tr(L"安装 Solaris", L"Install Solaris")
           : upgrade ? s.tr(L"更新 Solaris", L"Update Solaris") : s.tr(L"卸载 Solaris", L"Uninstall Solaris");
    case Phase::Working:
      return install ? s.tr(L"正在安装 Solaris", L"Installing Solaris")
           : upgrade ? s.tr(L"正在更新 Solaris", L"Updating Solaris") : s.tr(L"正在卸载 Solaris", L"Uninstalling Solaris");
    case Phase::Done:
      return install ? s.tr(L"Solaris 已安装", L"Solaris was installed")
           : upgrade ? s.tr(L"Solaris 已更新", L"Solaris was updated") : s.tr(L"Solaris 已卸载", L"Solaris was uninstalled");
    case Phase::Failed:
      return install ? s.tr(L"未能安装 Solaris", L"Couldn’t install Solaris")
           : upgrade ? s.tr(L"未能更新 Solaris", L"Couldn’t update Solaris") : s.tr(L"未能卸载 Solaris", L"Couldn’t uninstall Solaris");
  }
  return {};
}

std::wstring bodyText(const State& s) {
  const bool install = s.mode == Mode::Install, upgrade = s.mode == Mode::Upgrade;
  switch (s.phase) {
    case Phase::Ready:
      return install ? s.tr(L"安装 Solaris 应用和后台连接服务", L"Installs the Solaris app and its background connection service.")
           : upgrade ? s.tr(L"更新期间连接会短暂中断，订阅和设置会保留。", L"The connection pauses briefly during the update. Subscriptions and settings are kept.")
           : s.tr(L"卸载会断开当前连接，并移除 Solaris 应用和后台连接服务。", L"Uninstalling disconnects Solaris and removes the app and its background connection service.");
    case Phase::Working:
      return s.tr(L"完成前请保持此窗口打开。", L"Keep this window open until this finishes.");
    case Phase::Done:
      if (install) return s.tr(L"打开 Solaris，导入订阅或配置后即可连接。", L"Open Solaris and import a subscription or configuration to connect.");
      if (upgrade) return s.tr(L"打开 Solaris 即可继续使用。", L"Open Solaris to continue.");
      return s.purged ? s.tr(L"订阅、设置和日志已一并删除。", L"Subscriptions, settings and logs were deleted too.")
                      : s.tr(L"订阅、设置和日志仍保留在此电脑上，重新安装后可继续使用。", L"Subscriptions, settings and logs stay on this PC for when you reinstall.");
    case Phase::Failed:
      return s.failure;
  }
  return {};
}

std::wstring subtitleText(const State& s) {
  const std::wstring platform = L" · Windows " + s.architecture;
  if (s.mode == Mode::Upgrade && s.phase != Phase::Done && !s.installed.empty() && s.installed != s.version)
    return s.installed + L" → " + s.version + platform;
  return s.tr(L"版本 ", L"Version ") + s.version + platform;
}

// The steps NSIS reports through Step; installer.nsi names the same boundaries.
std::vector<std::wstring> stepNames(const State& s) {
  if (s.mode == Mode::Upgrade)
    return {s.tr(L"停止 Solaris 和后台服务", L"Stop Solaris and the background service"),
            s.tr(L"替换应用文件", L"Replace app files"), s.tr(L"注册 Solaris", L"Register Solaris"),
            s.tr(L"启动后台服务", L"Start the background service")};
  if (s.mode == Mode::Uninstall) {
    std::vector<std::wstring> names{s.tr(L"关闭 Solaris", L"Close Solaris"),
                                    s.tr(L"移除后台服务", L"Remove the background service"),
                                    s.tr(L"移除应用文件", L"Remove app files")};
    if (s.purged) names.emplace_back(s.tr(L"删除订阅、设置和日志", L"Delete subscriptions, settings and logs"));
    return names;
  }
  return {s.tr(L"检查安装位置", L"Check the install location"), s.tr(L"复制应用文件", L"Copy app files"),
          s.tr(L"注册 Solaris", L"Register Solaris"), s.tr(L"启动后台服务", L"Start the background service")};
}

Mark mark(const State& s, int index) {
  if (s.phase == Phase::Done || index < s.step) return Mark::Done;
  if (index > s.step) return Mark::Pending;
  return s.phase == Phase::Failed ? Mark::Failed : Mark::Current;
}

// The row's accessible name also carries what its icon shows.
std::wstring stepName(const State& s, int index) {
  const Mark value = mark(s, index);
  const wchar_t* status = value == Mark::Done ? s.tr(L"已完成", L"done")
                        : value == Mark::Current ? s.tr(L"进行中", L"in progress")
                        : value == Mark::Failed ? s.tr(L"失败", L"failed") : s.tr(L"等待", L"waiting");
  const auto& name = s.stepLabels[static_cast<size_t>(index)];
  return s.chinese ? name + L"（" + status + L"）" : name + L" (" + status + L")";
}

HWND defaultButton(const State& s) {
  if (s.licenseView) return s.primary;
  if (s.phase == Phase::Ready) return s.mode == Mode::Uninstall ? s.secondary : s.primary;
  if (s.phase == Phase::Done && s.mode != Mode::Uninstall) return s.primary;
  return s.secondary;
}

// ---- theme and fonts ------------------------------------------------------------------

void applyTheme(State& s) {
  const HWND window = s.window.load();
  if (!window) return;
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
    const COLORREF highlight = GetSysColor(COLOR_HIGHLIGHT), link = GetSysColor(COLOR_HOTLIGHT);
    Palette& p = s.palette;
    p.background = p.surface = p.surface_subtle = bg;
    p.surface_selected = GetSysColor(COLOR_BTNFACE);
    p.border = p.border_button = p.border_control = fg;
    p.text = p.text_secondary = p.text_muted = fg;
    p.primary = p.primary_hover = p.primary_pressed = highlight;
    p.on_primary = GetSysColor(COLOR_HIGHLIGHTTEXT);
    p.link = p.link_hover = link;
    // Status marks keep their glyphs; colour alone never carries the result.
    p.success = p.danger = highlight;
    p.control_track = GetSysColor(COLOR_GRAYTEXT);
  }
  BOOL animation = TRUE;
  SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &animation, 0);
  s.motion = animation && !s.highContrast;
  for (HBRUSH brush : {s.background, s.surface, s.subtle}) if (brush) DeleteObject(brush);
  s.background = CreateSolidBrush(s.palette.background);
  s.surface = CreateSolidBrush(s.palette.surface);
  s.subtle = CreateSolidBrush(s.palette.surface_subtle);
  const BOOL dark = s.dark && !s.highContrast;
  DwmSetWindowAttribute(window, DWMWA_USE_IMMERSIVE_DARK_MODE, &dark, sizeof(dark));
  // Windows 11 paints the caption in the content colour; Windows 10 rejects both attributes
  // and keeps its own caption. High contrast restores the system colours.
  const COLORREF caption = s.highContrast ? DWMWA_COLOR_DEFAULT : s.palette.surface;
  const COLORREF captionText = s.highContrast ? DWMWA_COLOR_DEFAULT : s.palette.text;
  DwmSetWindowAttribute(window, DWMWA_CAPTION_COLOR, &caption, sizeof(caption));
  DwmSetWindowAttribute(window, DWMWA_TEXT_COLOR, &captionText, sizeof(captionText));
  for (HWND edit : {s.licenseEdit, s.logEdit}) SetWindowTheme(edit, dark ? L"DarkMode_Explorer" : L"Explorer", nullptr);
  const bool spinning = s.phase == Phase::Working && s.motion && !s.showDetails;
  if (spinning) SetTimer(window, spinnerTimer, 33, nullptr);
  else KillTimer(window, spinnerTimer);
  RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_ERASE | RDW_FRAME);
}

HFONT font(State& s, int pixels, int weight, const wchar_t* family) {
  return CreateFontW(-s.px(pixels), 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                    OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                    DEFAULT_PITCH | FF_DONTCARE, family);
}

int CALLBACK fontFound(const LOGFONTW*, const TEXTMETRICW*, DWORD, LPARAM data) {
  *reinterpret_cast<bool*>(data) = true;
  return 0;
}

bool hasFont(const wchar_t* family) {
  LOGFONTW query{};
  query.lfCharSet = DEFAULT_CHARSET;
  wcscpy_s(query.lfFaceName, family);
  bool found = false;
  HDC dc = GetDC(nullptr);
  if (dc) {
    EnumFontFamiliesExW(dc, &query, fontFound, reinterpret_cast<LPARAM>(&found), 0);
    ReleaseDC(nullptr, dc);
  }
  return found;
}

HICON loadIcon(State& s, int size) {
  return static_cast<HICON>(LoadImageW(nullptr, (s.assets + L"\\solaris.ico").c_str(), IMAGE_ICON,
                                       s.px(size), s.px(size), LR_LOADFROMFILE));
}

void makeFonts(State& s) {
  const wchar_t* family = s.privateFont ? L"Inter" : L"Segoe UI";
  if (s.chinese) {
    // English/Server images may lack the optional Chinese UI fonts. Do not let
    // GDI choose an unrelated Latin serif face when the requested font is absent.
    family = hasFont(L"Microsoft YaHei UI") ? L"Microsoft YaHei UI"
           : hasFont(L"Microsoft YaHei") ? L"Microsoft YaHei" : L"Segoe UI";
  }
  const HFONT previous[] = {s.titleFont, s.bodyFont, s.strongFont, s.captionFont, s.sectionFont, s.labelFont, s.monoFont};
  s.titleFont = font(s, kTitleSize, kTitleWeight, family);
  s.bodyFont = font(s, kBodySize, kBodyWeight, family);
  s.strongFont = font(s, kStrongSize, kStrongWeight, family);
  s.captionFont = font(s, kCaptionSize, kCaptionWeight, family);
  s.sectionFont = font(s, kSectionSize, kSectionWeight, family);
  s.labelFont = font(s, kLabelSize, kLabelWeight, family);
  s.monoFont = font(s, kMonoSize, kMonoWeight, s.privateMono ? L"JetBrains Mono" : L"Consolas");
  const auto set = [](HWND control, HFONT value) { SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(value), FALSE); };
  for (HWND control : {s.body, s.pathEdit, s.locationKey, s.locationValue, s.versionKey, s.versionValue,
                       s.purge, s.agreement, s.link, s.detailButton}) set(control, s.bodyFont);
  for (HWND control : {s.subtitle, s.purgeNote, s.licenseNote, s.logLabel}) set(control, s.captionFont);
  for (HWND control : {s.primary, s.secondary, s.browse}) set(control, s.labelFont);
  for (HWND control : s.steps) set(control, s.bodyFont);
  set(s.title, s.titleFont);
  set(s.licenseTitle, s.titleFont);
  set(s.pathLabel, s.sectionFont);
  set(s.licenseEdit, s.monoFont);
  // JetBrains Mono has no CJK glyphs, and EDIT controls do not fall back for a private font.
  set(s.logEdit, s.chinese ? s.captionFont : s.monoFont);
  for (HFONT old : previous) if (old) DeleteObject(old);
  const HICON oldIcons[] = {s.icon, s.bigIcon, s.smallIcon};
  s.icon = loadIcon(s, iconSize);
  s.bigIcon = loadIcon(s, 32);
  s.smallIcon = loadIcon(s, 16);
  SendMessageW(s.window.load(), WM_SETICON, ICON_BIG, reinterpret_cast<LPARAM>(s.bigIcon));
  SendMessageW(s.window.load(), WM_SETICON, ICON_SMALL, reinterpret_cast<LPARAM>(s.smallIcon));
  for (HICON old : oldIcons) if (old) DestroyIcon(old);
}

// ---- layout and state -----------------------------------------------------------------

void place(HWND control, int x, int y, int width, int height) {
  SetWindowPos(control, nullptr, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE);
}

// Read-only text sits in a bordered box; the edit leaves the rounded corners to the parent.
void placeReader(State& s, HWND edit, RECT box, int paddingX, int paddingY) {
  const int inset = s.px(3);
  place(edit, box.left + inset, box.top + inset, box.right - box.left - 2 * inset, box.bottom - box.top - 2 * inset);
  RECT client{};
  GetClientRect(edit, &client);
  RECT format{s.px(paddingX) - inset, s.px(paddingY) - inset, client.right - s.px(4), client.bottom - s.px(4)};
  SendMessageW(edit, EM_SETRECT, 0, reinterpret_cast<LPARAM>(&format));
}

int buttonSize(State& s, HWND button) {
  wchar_t label[64]{};
  GetWindowTextW(button, label, 64);
  return std::max(s.px(buttonWidth), static_cast<int>(measure(s, s.labelFont, label).cx) + s.px(32));
}

void layout(State& s) {
  const int m = s.px(kMargin), width = s.px(kWidth - 2 * kMargin);
  const int textLeft = s.px(kMargin + iconSize + 16), textWidth = s.px(kWidth - kMargin) - textLeft;
  const int bottom = s.px(kHeight - footerHeight - kMargin);
  place(s.title, textLeft, s.px(18), textWidth, s.px(25));
  place(s.subtitle, textLeft, s.px(45), textWidth, s.px(18));
  // NSIS's error text may be long; the full message is also in the details.
  const int bodyHeight = std::min(wrappedHeight(s, s.bodyFont, bodyText(s), width), 4 * lineHeight(s, s.bodyFont));
  place(s.body, m, s.px(bodyTop), width, bodyHeight);
  const int below = s.px(bodyTop) + bodyHeight;

  int y = below + s.px(24);
  place(s.pathLabel, m, y, width, s.px(18));
  y += s.px(25);
  s.field = {m, y, m + width - s.px(browseWidth + 8), y + s.px(kButtonHeight)};
  const int editHeight = lineHeight(s, s.bodyFont);
  place(s.pathEdit, s.field.left + s.px(10), y + (s.px(kButtonHeight) - editHeight) / 2,
        s.field.right - s.field.left - s.px(20), editHeight);
  place(s.browse, m + width - s.px(browseWidth), y, s.px(browseWidth), s.px(kButtonHeight));

  wchar_t key[64]{};
  int keyWidth = s.px(88);
  for (HWND control : {s.locationKey, s.versionKey}) {
    GetWindowTextW(control, key, 64);
    keyWidth = std::max(keyWidth, static_cast<int>(measure(s, s.bodyFont, key).cx) + s.px(16));
  }
  y = below + s.px(24);
  for (auto [name, value] : {std::pair{s.locationKey, s.locationValue}, std::pair{s.versionKey, s.versionValue}}) {
    place(name, m, y, keyWidth, s.px(21));
    place(value, m + keyWidth, y, width - keyWidth, s.px(21));
    y += s.px(31);
  }

  y = below + s.px(24);
  place(s.purge, m, y, width, s.px(checkRow));
  place(s.purgeNote, m + s.px(28), y + s.px(checkRow + 4), width - s.px(28), s.px(18));

  y = bottom - s.px(checkRow);
  const int agreementWidth = s.px(28) + measure(s, s.bodyFont, s.agreementLabel).cx + s.px(2);
  place(s.agreement, m, y, agreementWidth, s.px(checkRow));
  const SIZE link = measure(s, s.bodyFont, s.linkLabel);
  place(s.link, m + agreementWidth + s.px(2), y + (s.px(checkRow) - link.cy) / 2 - 1, link.cx + 4, link.cy + 2);

  for (int i = 0; i < stepLimit; ++i)
    place(s.steps[i], m, below + s.px(20 + i * rowPitch), width, s.px(rowHeight));
  place(s.logLabel, m, below + s.px(20), 0, 0);
  const RECT logBox{m, below + s.px(20), m + width, bottom};
  placeReader(s, s.logEdit, logBox, 12, 10);

  place(s.licenseTitle, m, s.px(16), width, s.px(25));
  place(s.licenseNote, m, s.px(43), width, s.px(18));
  const RECT licenseBox{m, s.px(76), m + width, bottom};
  placeReader(s, s.licenseEdit, licenseBox, 14, 12);
  s.box = s.licenseView ? licenseBox : logBox;

  const int buttonTop = s.px(kHeight - footerHeight) + (s.px(footerHeight) - s.px(kButtonHeight)) / 2;
  int right = s.px(kWidth - kMargin);
  for (HWND button : {s.secondary, s.primary}) {
    if (!shown(button)) continue;
    const int size = buttonSize(s, button);
    place(button, right - size, buttonTop, size, s.px(kButtonHeight));
    right -= size + s.px(8);
  }
  wchar_t label[64]{};
  GetWindowTextW(s.detailButton, label, 64);
  place(s.detailButton, m - s.px(4), buttonTop, s.px(26) + measure(s, s.bodyFont, label).cx, s.px(kButtonHeight));
}

void appendLog(State& s, const std::wstring& line) {
  if (line.empty()) return;
  if (!s.log.empty()) s.log += L"\r\n";
  for (wchar_t ch : line) {
    if (ch == L'\n' && (s.log.empty() || s.log.back() != L'\r')) s.log += L'\r';
    s.log += ch;
  }
  if (s.log.size() > 32768) s.log.erase(0, s.log.size() - 32768);
  text(s.logEdit, s.log);
  SendMessageW(s.logEdit, EM_SETSEL, static_cast<WPARAM>(-1), static_cast<LPARAM>(-1));
  SendMessageW(s.logEdit, EM_SCROLLCARET, 0, 0);
}

void render(State& s) {
  const HWND window = s.window.load();
  const bool ready = s.phase == Phase::Ready, busy = s.phase == Phase::Working;
  const bool done = s.phase == Phase::Done, install = s.mode == Mode::Install;
  const bool upgrade = s.mode == Mode::Upgrade, uninstall = s.mode == Mode::Uninstall, license = s.licenseView;
  text(s.title, headline(s));
  text(s.subtitle, subtitleText(s));
  text(s.body, bodyText(s));
  s.stepLabels = stepNames(s);
  const int count = static_cast<int>(s.stepLabels.size());
  for (int i = 0; i < count; ++i) text(s.steps[i], stepName(s, i));
  for (HWND control : {s.title, s.subtitle, s.body}) show(control, !license);
  for (HWND control : {s.pathLabel, s.pathEdit, s.browse}) show(control, !license && ready && install);
  for (HWND control : {s.locationKey, s.locationValue, s.versionKey, s.versionValue}) show(control, !license && ready && upgrade);
  for (HWND control : {s.purge, s.purgeNote}) show(control, ready && uninstall);
  for (HWND control : {s.agreement, s.link}) show(control, !license && ready && !uninstall);
  for (HWND control : {s.licenseNote, s.licenseTitle, s.licenseEdit}) show(control, license);
  for (int i = 0; i < stepLimit; ++i) show(s.steps[i], !ready && !s.showDetails && i < count);
  for (HWND control : {s.logLabel, s.logEdit}) show(control, !ready && s.showDetails);
  show(s.detailButton, !ready);
  text(s.detailButton, s.showDetails ? s.tr(L"隐藏详细信息", L"Hide details") : s.tr(L"显示详细信息", L"Show details"));
  if (license) {
    text(s.primary, s.tr(L"同意", L"Agree"));
    text(s.secondary, s.tr(L"返回", L"Back"));
  } else if (ready) {
    text(s.primary, install ? s.tr(L"安装", L"Install") : upgrade ? s.tr(L"更新", L"Update") : s.tr(L"卸载", L"Uninstall"));
    text(s.secondary, s.tr(L"取消", L"Cancel"));
  } else if (!busy) {
    // While hidden, the action keeps its name until the outcome: smoke-test.ps1 waits for it.
    text(s.primary, done ? s.tr(L"打开 Solaris", L"Open Solaris") : s.tr(L"关闭", L"Close"));
    text(s.secondary, s.tr(L"关闭", L"Close"));
  }
  show(s.primary, license || ready || (done && !uninstall));
  show(s.secondary, !busy);
  EnableWindow(s.primary, license || !ready || uninstall || checked(s.agreement));
  layout(s);
  if (busy && s.motion && !s.showDetails) SetTimer(window, spinnerTimer, 33, nullptr);
  else KillTimer(window, spinnerTimer);
  EnableMenuItem(GetSystemMenu(window, FALSE), SC_CLOSE, MF_BYCOMMAND | (busy ? MF_GRAYED : MF_ENABLED));
  DrawMenuBar(window);
  RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_ERASE);
}

// ---- painting -------------------------------------------------------------------------

void hover(State& s, HWND window, UINT message) {
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
}

void chevron(State& s, HDC dc, float x, float y, COLORREF ink, bool open) {
  Gdiplus::Graphics graphics(dc);
  smooth(graphics);
  if (open) glyph(graphics, x, y, s.scale(), ink, 1.5f, {{9.5f, 4.5f}, {6.0f, 8.0f}, {2.5f, 4.5f}});
  else glyph(graphics, x, y, s.scale(), ink, 1.5f, {{4.5f, 2.5f}, {8.0f, 6.0f}, {4.5f, 9.5f}});
}

void drawButton(State& s, const DRAWITEMSTRUCT& item) {
  const RECT rect = item.rcItem;
  buffered(item.hDC, rect, [&](HDC dc) {
    const bool disabled = (item.itemState & ODS_DISABLED) != 0;
    const bool pressed = (item.itemState & ODS_SELECTED) != 0;
    const bool hot = s.hovered == item.hwndItem;
    const bool browse = item.CtlID == browseID;
    const COLORREF base = browse ? s.palette.surface : s.palette.background;
    fill(dc, rect, base);
    wchar_t label[64]{};
    GetWindowTextW(item.hwndItem, label, 64);
    SetBkMode(dc, TRANSPARENT);
    RECT area = rect;
    if (item.CtlID == detailsID) {
      const COLORREF ink = hot || pressed ? s.palette.link_hover : s.palette.link;
      chevron(s, dc, static_cast<float>(s.px(4)), static_cast<float>((rect.bottom - s.px(12)) / 2), ink, s.showDetails);
      area.left = s.px(22);
      SetTextColor(dc, ink);
      const auto old = SelectObject(dc, s.bodyFont);
      DrawTextW(dc, label, -1, &area, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
      SelectObject(dc, old);
    } else {
      COLORREF paint, border, ink;
      if (item.hwndItem == defaultButton(s)) {
        paint = pressed ? s.palette.primary_pressed : hot ? s.palette.primary_hover : s.palette.primary;
        border = paint;
        ink = s.palette.on_primary;
      } else {
        // Standard buttons are white on the light footer and one step up on the dark one.
        const COLORREF rest = browse || s.dark ? s.palette.surface_subtle : s.palette.surface;
        paint = pressed ? s.palette.surface_selected : hot ? (rest == s.palette.surface ? s.palette.surface_subtle : s.palette.surface_selected) : rest;
        border = s.palette.border_button;
        ink = s.palette.text;
      }
      if (disabled) {
        paint = mix(paint, base, kDisabledOpacity);
        border = mix(border, base, kDisabledOpacity);
        ink = s.highContrast ? GetSysColor(COLOR_GRAYTEXT) : mix(ink, base, kDisabledOpacity);
      }
      roundBox(dc, rect, paint, border, static_cast<float>(s.px(kRadiusSm)));
      SetTextColor(dc, ink);
      const auto old = SelectObject(dc, s.labelFont);
      DrawTextW(dc, label, -1, &area, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX);
      SelectObject(dc, old);
    }
    if ((item.itemState & ODS_FOCUS) && !focusHidden(s)) {
      RECT focus = rect;
      InflateRect(&focus, -s.px(3), -s.px(3));
      DrawFocusRect(dc, &focus);
    }
  });
}

LRESULT CALLBACK buttonProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR data) {
  hover(*reinterpret_cast<State*>(data), window, message);
  return DefSubclassProc(window, message, wparam, lparam);
}

void drawCheckbox(State& s, HWND window, HDC target) {
  RECT rect{};
  GetClientRect(window, &rect);
  buffered(target, rect, [&](HDC dc) {
    fill(dc, rect, s.palette.surface);
    const float f = s.scale();
    const int top = (rect.bottom - s.px(18)) / 2;
    RECT box{0, top, s.px(18), top + s.px(18)};
    const bool on = checked(window);
    roundBox(dc, box, on ? s.palette.primary : s.palette.surface_subtle,
             on ? s.palette.primary : s.palette.border_control, static_cast<float>(s.px(kRadiusXs)), 1.5f * f);
    if (on) {
      Gdiplus::Graphics graphics(dc);
      smooth(graphics);
      glyph(graphics, 0.0f, static_cast<float>(top), f, s.palette.on_primary, 1.8f,
            {{4.6f, 9.2f}, {7.4f, 12.0f}, {13.4f, 5.8f}});
    }
    wchar_t caption[256]{};
    GetWindowTextW(window, caption, 256);
    const std::wstring label = window == s.agreement ? s.agreementLabel : caption;
    RECT area{s.px(28), 0, rect.right, rect.bottom};
    const auto old = SelectObject(dc, s.bodyFont);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, s.palette.text);
    DrawTextW(dc, label.c_str(), -1, &area, DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX);
    if (GetFocus() == window && !focusHidden(s)) {
      RECT focus = area;
      DrawTextW(dc, label.c_str(), -1, &focus, DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | DT_CALCRECT);
      focus.top = 0;
      focus.bottom = rect.bottom;
      InflateRect(&focus, s.px(2), 0);
      focus.right = std::min(focus.right, rect.right);
      DrawFocusRect(dc, &focus);
    }
    SelectObject(dc, old);
  });
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
  const LRESULT result = DefSubclassProc(window, message, wparam, lparam);
  if (message == BM_SETCHECK || message == BM_SETSTATE || message == WM_SETFOCUS || message == WM_KILLFOCUS ||
      message == WM_ENABLE || message == WM_UPDATEUISTATE)
    InvalidateRect(window, nullptr, FALSE);
  return result;
}

// SysLink keeps its keyboard, mouse and accessibility behaviour; it cannot take the palette's
// link colour, so its single link is painted here at the position SysLink lays it out.
void drawLink(State& s, HWND window, HDC target) {
  RECT rect{};
  GetClientRect(window, &rect);
  buffered(target, rect, [&](HDC dc) {
    fill(dc, rect, s.palette.surface);
    const COLORREF ink = s.hovered == window ? s.palette.link_hover : s.palette.link;
    const auto old = SelectObject(dc, s.bodyFont);
    TEXTMETRICW metrics{};
    GetTextMetricsW(dc, &metrics);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, ink);
    RECT area{2, 1, rect.right, rect.bottom};
    DrawTextW(dc, s.linkLabel.c_str(), -1, &area, DT_SINGLELINE | DT_NOPREFIX);
    const int thickness = std::max(1, s.px(1));
    const int line = std::min(1 + static_cast<int>(metrics.tmAscent) + s.px(2), static_cast<int>(rect.bottom) - thickness);
    fill(dc, {2, line, rect.right - 2, line + thickness}, ink);
    if (GetFocus() == window && !focusHidden(s)) DrawFocusRect(dc, &rect);
    SelectObject(dc, old);
  });
}

LRESULT CALLBACK linkProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR data) {
  auto& s = *reinterpret_cast<State*>(data);
  hover(s, window, message);
  if (message == WM_ERASEBKGND) return 1;
  if (message == WM_PAINT) {
    PAINTSTRUCT paint{};
    HDC dc = BeginPaint(window, &paint);
    drawLink(s, window, dc);
    EndPaint(window, &paint);
    return 0;
  }
  if (message == WM_PRINTCLIENT) { drawLink(s, window, reinterpret_cast<HDC>(wparam)); return 0; }
  const LRESULT result = DefSubclassProc(window, message, wparam, lparam);
  if (message == WM_SETFOCUS || message == WM_KILLFOCUS || message == WM_UPDATEUISTATE) InvalidateRect(window, nullptr, FALSE);
  return result;
}

// Focus moves the parent's field border; read-only text shows no insertion caret.
LRESULT CALLBACK editProcedure(HWND window, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR, DWORD_PTR readOnly) {
  const LRESULT result = DefSubclassProc(window, message, wparam, lparam);
  if (message == WM_SETFOCUS && readOnly) DestroyCaret();
  if (message == WM_SETFOCUS || message == WM_KILLFOCUS) {
    if (const HWND parent = GetParent(window)) InvalidateRect(parent, nullptr, FALSE);
  }
  return result;
}

void drawMark(State& s, Gdiplus::Graphics& graphics, float x, float y, Mark value) {
  const float f = s.scale();
  const COLORREF ink = s.palette.on_primary;
  switch (value) {
    case Mark::Done:
      disc(graphics, x, y, 18.0f * f, s.palette.success);
      glyph(graphics, x, y, f, ink, 1.8f, {{5.2f, 9.3f}, {7.6f, 11.7f}, {12.6f, 6.5f}});
      break;
    case Mark::Failed:
      disc(graphics, x, y, 18.0f * f, s.palette.danger);
      glyph(graphics, x, y, f, ink, 1.8f, {{6.2f, 6.2f}, {11.8f, 11.8f}});
      glyph(graphics, x, y, f, ink, 1.8f, {{11.8f, 6.2f}, {6.2f, 11.8f}});
      break;
    case Mark::Current: {
      Gdiplus::Pen track(color(s.palette.control_track), 2.0f * f);
      graphics.DrawEllipse(&track, x + 2.0f * f, y + 2.0f * f, 14.0f * f, 14.0f * f);
      Gdiplus::Pen arc(color(s.palette.primary), 2.0f * f);
      arc.SetLineCap(Gdiplus::LineCapRound, Gdiplus::LineCapRound, Gdiplus::DashCapRound);
      // One turn per 0.9 s; without animations it is the prototype's first frame.
      const float angle = s.motion ? static_cast<float>(GetTickCount64() % 900) * 0.4f : 0.0f;
      graphics.DrawArc(&arc, x + 2.0f * f, y + 2.0f * f, 14.0f * f, 14.0f * f, angle - 90.0f, 90.0f);
      break;
    }
    case Mark::Pending: {
      Gdiplus::Pen ring(color(s.palette.border_button), 1.5f * f);
      graphics.DrawEllipse(&ring, x + 1.75f * f, y + 1.75f * f, 14.5f * f, 14.5f * f);
      break;
    }
  }
}

void drawStep(State& s, const DRAWITEMSTRUCT& item) {
  const int index = static_cast<int>(item.CtlID) - firstStepID;
  if (index < 0 || index >= static_cast<int>(s.stepLabels.size())) return;
  const RECT rect = item.rcItem;
  buffered(item.hDC, rect, [&](HDC dc) {
    fill(dc, rect, s.palette.surface);
    const Mark value = mark(s, index);
    {
      Gdiplus::Graphics graphics(dc);
      smooth(graphics);
      drawMark(s, graphics, 0.0f, static_cast<float>(rect.bottom - s.px(18)) / 2.0f, value);
    }
    RECT area{s.px(30), 0, rect.right, rect.bottom};
    const bool strong = value == Mark::Current || value == Mark::Failed;
    const auto old = SelectObject(dc, strong ? s.strongFont : s.bodyFont);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, value == Mark::Pending ? s.palette.text_muted : s.palette.text);
    DrawTextW(dc, s.stepLabels[static_cast<size_t>(index)].c_str(), -1, &area,
              DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | DT_END_ELLIPSIS);
    SelectObject(dc, old);
  });
}

void drawSurface(State& s, HWND window, HDC target) {
  RECT client{};
  GetClientRect(window, &client);
  buffered(target, client, [&](HDC dc) {
    const int footer = s.px(kHeight - footerHeight);
    fill(dc, {0, 0, client.right, footer}, s.palette.surface);
    fill(dc, {0, footer, client.right, client.bottom}, s.palette.background);
    fill(dc, {0, footer, client.right, footer + 1}, s.palette.border);
    const bool ready = s.phase == Phase::Ready;
    if (!s.licenseView) {
      const int x = s.px(kMargin), y = s.px(16);
      if (s.icon) DrawIconEx(dc, x, y, s.icon, s.px(iconSize), s.px(iconSize), 0, nullptr, DI_NORMAL);
      if (s.phase == Phase::Done || s.phase == Phase::Failed) {
        // A 20px status badge over the icon's lower right, ringed in the content colour.
        const float f = s.scale();
        const float bx = static_cast<float>(x) + 33.0f * f, by = static_cast<float>(y) + 33.0f * f;
        Gdiplus::Graphics graphics(dc);
        smooth(graphics);
        disc(graphics, bx, by, 20.0f * f, s.palette.surface);
        disc(graphics, bx + 2.0f * f, by + 2.0f * f, 16.0f * f, s.phase == Phase::Done ? s.palette.success : s.palette.danger);
        if (s.phase == Phase::Done) {
          glyph(graphics, bx + 5.0f * f, by + 5.0f * f, f, s.palette.on_primary, 1.8f, {{1.8f, 5.2f}, {3.9f, 7.3f}, {8.2f, 2.7f}});
        } else {
          glyph(graphics, bx + 5.0f * f, by + 5.0f * f, f, s.palette.on_primary, 1.8f, {{2.3f, 2.3f}, {7.7f, 7.7f}});
          glyph(graphics, bx + 5.0f * f, by + 5.0f * f, f, s.palette.on_primary, 1.8f, {{7.7f, 2.3f}, {2.3f, 7.7f}});
        }
      }
    }
    if (!s.licenseView && ready && s.mode == Mode::Install) {
      const bool focused = GetFocus() == s.pathEdit;
      const RECT field = s.field;
      roundBox(dc, field, s.palette.surface_subtle, focused ? s.palette.primary : s.palette.border_button,
               static_cast<float>(s.px(kRadiusXs)));
      // The Windows text field's accent edge: 1px at rest, 2px with focus.
      const int edge = focused ? std::max(2, s.px(2)) : 1;
      fill(dc, {field.left + s.px(kRadiusXs), field.bottom - edge, field.right - s.px(kRadiusXs), field.bottom},
           focused ? s.palette.primary : s.palette.border_control);
    }
    if (s.licenseView || (!ready && s.showDetails)) {
      const bool focused = GetFocus() == (s.licenseView ? s.licenseEdit : s.logEdit);
      roundBox(dc, s.box, s.palette.surface_subtle, focused ? s.palette.primary : s.palette.border,
               static_cast<float>(s.px(kRadiusSm)), focused ? static_cast<float>(std::max(2, s.px(2))) : 1.0f);
    }
  });
}

// ---- controls -------------------------------------------------------------------------

HWND control(State& s, const wchar_t* type, const wchar_t* caption, DWORD style, int id = 0) {
  HWND window = CreateWindowExW(0, type, caption, WS_CHILD | WS_VISIBLE | style, 0, 0, 0, 0,
                               s.window.load(), reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), module, nullptr);
  if (!window) throw std::runtime_error("create control");
  return window;
}

// The GPL text as distributed, without the centring and paragraph indents a narrow reader
// cannot show.
std::wstring readLicense(const std::wstring& path) {
  std::unique_ptr<void, decltype(&CloseHandle)> file(
      CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr), &CloseHandle);
  if (file.get() == INVALID_HANDLE_VALUE) throw std::runtime_error("license file");
  const DWORD length = GetFileSize(file.get(), nullptr);
  if (!length || length > 131072) throw std::runtime_error("license size");
  std::vector<char> bytes(length);
  DWORD read = 0;
  if (!ReadFile(file.get(), bytes.data(), length, &read, nullptr) || read != length) throw std::runtime_error("license read");
  const int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(), static_cast<int>(length), nullptr, 0);
  if (count < 1) throw std::runtime_error("license encoding");
  std::wstring value(static_cast<size_t>(count), L'\0');
  MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes.data(), static_cast<int>(length), value.data(), count);
  std::wstring result;
  bool lineStart = true;
  for (wchar_t ch : value) {
    if (ch == L'\r') continue;
    if (ch == L'\n') { result += L"\r\n"; lineStart = true; continue; }
    if (lineStart && (ch == L' ' || ch == L'\t')) continue;
    lineStart = false;
    result += ch;
  }
  return result;
}

void chooseDirectory(State& s) {
  Microsoft::WRL::ComPtr<IFileOpenDialog> dialog;
  if (FAILED(CoCreateInstance(CLSID_FileOpenDialog, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(dialog.GetAddressOf())))) return;
  dialog->SetOptions(FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_NOCHANGEDIR);
  dialog->SetTitle(s.tr(L"选择安装位置（将创建 Solaris 文件夹）", L"Choose a folder for Solaris"));
  const auto slash = s.directory.find_last_of(L'\\');
  Microsoft::WRL::ComPtr<IShellItem> folder;
  if (slash != std::wstring::npos && SUCCEEDED(SHCreateItemFromParsingName(s.directory.substr(0, slash + 1).c_str(),
      nullptr, IID_PPV_ARGS(folder.GetAddressOf())))) dialog->SetFolder(folder.Get());
  if (FAILED(dialog->Show(s.window.load()))) return;
  Microsoft::WRL::ComPtr<IShellItem> selected;
  PWSTR path = nullptr;
  if (FAILED(dialog->GetResult(selected.GetAddressOf())) || FAILED(selected->GetDisplayName(SIGDN_FILESYSPATH, &path))) return;
  std::wstring directory(path);
  CoTaskMemFree(path);
  while (!directory.empty() && directory.back() == L'\\') directory.pop_back();
  const auto leaf = directory.substr(directory.find_last_of(L'\\') + 1);
  if (_wcsicmp(leaf.c_str(), L"Solaris") != 0) directory += L"\\Solaris";
  text(s.pathEdit, directory);
  SetFocus(s.pathEdit);
}

bool selectedDirectory(State& s) {
  if (s.mode != Mode::Install) return true;
  wchar_t input[1024]{}, full[1024]{};
  GetWindowTextW(s.pathEdit, input, 1024);
  std::wstring value(input);
  std::replace(value.begin(), value.end(), L'/', L'\\');
  const bool drive = value.size() > 3 && ((value[0] >= L'A' && value[0] <= L'Z') || (value[0] >= L'a' && value[0] <= L'z'))
                     && value[1] == L':' && value[2] == L'\\';
  const DWORD length = drive && value.substr(2).find_first_of(L"<>:\"|?*\r\n") == std::wstring::npos
                       ? GetFullPathNameW(value.c_str(), 1024, full, nullptr) : 0;
  if (!length || length >= 1024) {
    MessageBoxW(s.window.load(), s.tr(L"请选择本地磁盘上的完整文件夹路径，例如 D:\\Solaris。", L"Choose an absolute folder on a local drive, such as D:\\Solaris."),
                L"Solaris", MB_OK | MB_ICONINFORMATION);
    SetFocus(s.pathEdit);
    return false;
  }
  s.directory = full;
  while (s.directory.size() > 3 && s.directory.back() == L'\\') s.directory.pop_back();
  return true;
}

// Z-order is tab order, and a native EDIT takes its accessible name from the static just before it.
void makeControls(State& s) {
  s.title = control(s, L"STATIC", L"", SS_NOPREFIX | SS_ENDELLIPSIS);
  s.subtitle = control(s, L"STATIC", L"", SS_NOPREFIX | SS_ENDELLIPSIS, subtitleID);
  s.body = control(s, L"STATIC", L"", SS_NOPREFIX | SS_EDITCONTROL);
  s.pathLabel = control(s, L"STATIC", s.tr(L"安装位置", L"Install location"), SS_NOPREFIX);
  s.pathEdit = control(s, L"EDIT", s.directory.c_str(), ES_AUTOHSCROLL | WS_TABSTOP, pathID);
  SetWindowSubclass(s.pathEdit, editProcedure, 1, 0);
  SendMessageW(s.pathEdit, EM_LIMITTEXT, 1000, 0);
  s.browse = control(s, L"BUTTON", s.tr(L"浏览…", L"Browse…"), BS_OWNERDRAW | WS_TABSTOP, browseID);
  s.locationKey = control(s, L"STATIC", s.tr(L"安装位置", L"Location"), SS_NOPREFIX);
  s.locationValue = control(s, L"STATIC", s.directory.c_str(), SS_NOPREFIX | SS_PATHELLIPSIS);
  s.versionKey = control(s, L"STATIC", s.tr(L"当前版本", L"Current version"), SS_NOPREFIX);
  s.versionValue = control(s, L"STATIC", s.installed.empty() ? L"—" : s.installed.c_str(), SS_NOPREFIX);
  s.purge = control(s, L"BUTTON", s.tr(L"同时删除订阅、设置和日志", L"Also delete subscriptions, settings and logs"),
                    BS_AUTOCHECKBOX | WS_TABSTOP, purgeID);
  SetWindowSubclass(s.purge, checkboxProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  SendMessageW(s.purge, BM_SETCHECK, s.purgeDefault ? BST_CHECKED : BST_UNCHECKED, 0);
  s.purgeNote = control(s, L"STATIC", s.tr(L"包括此电脑上所有用户的 Solaris 数据，删除后无法恢复。",
                                           L"Includes Solaris data of every user on this PC. This can’t be undone."), SS_NOPREFIX);
  for (int i = 0; i < stepLimit; ++i) s.steps[i] = control(s, L"STATIC", L"", SS_OWNERDRAW | SS_NOPREFIX, firstStepID + i);
  s.logLabel = control(s, L"STATIC", s.tr(L"操作详情", L"Operation details"), SS_NOPREFIX);
  s.logEdit = control(s, L"EDIT", L"", ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | WS_VSCROLL | WS_TABSTOP, logID);
  SetWindowSubclass(s.logEdit, editProcedure, 1, 1);
  SendMessageW(s.logEdit, EM_LIMITTEXT, 32768, 0);
  s.licenseNote = control(s, L"STATIC", s.tr(L"Solaris 以 GPL-3.0-only 发布，以下是许可证原文。",
                                             L"Solaris is released under GPL-3.0-only. The license text follows."), SS_NOPREFIX);
  s.licenseTitle = control(s, L"STATIC", s.tr(L"GPLv3 开源许可协议", L"GPLv3 open-source license"), SS_NOPREFIX);
  s.licenseEdit = control(s, L"EDIT", L"", ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | WS_VSCROLL | WS_TABSTOP, licenseTextID);
  SendMessageW(s.licenseEdit, EM_LIMITTEXT, 131072, 0);
  text(s.licenseEdit, s.license);
  SetWindowSubclass(s.licenseEdit, editProcedure, 1, 1);
  s.agreementLabel = s.tr(L"我已阅读并同意", L"I have read and agree to the");
  s.linkLabel = s.tr(L"GPLv3 开源许可协议", L"GPLv3 open-source license");
  s.agreement = control(s, L"BUTTON", (s.agreementLabel + L" " + s.linkLabel).c_str(), BS_AUTOCHECKBOX | WS_TABSTOP, agreementID);
  SetWindowSubclass(s.agreement, checkboxProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  s.link = control(s, WC_LINK, (L"<a>" + s.linkLabel + L"</a>").c_str(), WS_TABSTOP, licenseID);
  SetWindowSubclass(s.link, linkProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  s.detailButton = control(s, L"BUTTON", L"", BS_OWNERDRAW | WS_TABSTOP, detailsID);
  s.primary = control(s, L"BUTTON", L"", BS_OWNERDRAW | WS_TABSTOP, actionID);
  s.secondary = control(s, L"BUTTON", L"", BS_OWNERDRAW | WS_TABSTOP, cancelID);
  for (HWND button : {s.primary, s.secondary, s.detailButton, s.browse})
    SetWindowSubclass(button, buttonProcedure, 1, reinterpret_cast<DWORD_PTR>(&s));
  makeFonts(s);
  applyTheme(s);
  render(s);
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

void openLicense(State& s) {
  if (s.phase != Phase::Ready || s.mode == Mode::Uninstall) return;
  s.licenseView = true;
  render(s);
  SetFocus(s.licenseEdit);
}

void closeLicense(State& s, bool agree) {
  if (agree) SendMessageW(s.agreement, BM_SETCHECK, BST_CHECKED, 0);
  s.licenseView = false;
  render(s);
  SetFocus(agree ? s.primary : s.link);
}

void start(State& s) {
  if (s.mode != Mode::Uninstall && !checked(s.agreement)) return;
  if (!selectedDirectory(s)) return;
  s.purged = s.mode == Mode::Uninstall && checked(s.purge);
  s.result.store(s.purged ? 2 : 1);
  s.phase = Phase::Working;
  s.step = 0;
  appendLog(s, (s.mode == Mode::Uninstall ? s.tr(L"卸载位置：", L"Uninstall location: ")
                                          : s.tr(L"安装位置：", L"Install location: ")) + s.directory);
  render(s);
  SetFocus(s.detailButton);
  SetEvent(s.action);
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
    case WM_CTLCOLOREDIT: {
      HDC dc = reinterpret_cast<HDC>(wparam);
      HWND child = reinterpret_cast<HWND>(lparam);
      const bool boxed = child == s.pathEdit || child == s.licenseEdit || child == s.logEdit;
      COLORREF ink = s.palette.text;
      if (child == s.body || child == s.pathLabel || child == s.licenseEdit || child == s.logEdit) ink = s.palette.text_secondary;
      else if (child == s.subtitle || child == s.purgeNote || child == s.licenseNote || child == s.locationKey || child == s.versionKey)
        ink = s.palette.text_muted;
      SetTextColor(dc, ink);
      SetBkColor(dc, boxed ? s.palette.surface_subtle : s.palette.surface);
      return reinterpret_cast<LRESULT>(boxed ? s.subtle : s.surface);
    }
    case WM_DRAWITEM: {
      const auto& item = *reinterpret_cast<DRAWITEMSTRUCT*>(lparam);
      if (item.CtlType == ODT_STATIC) drawStep(s, item);
      else drawButton(s, item);
      return TRUE;
    }
    case WM_NOTIFY: {
      const auto& header = *reinterpret_cast<NMHDR*>(lparam);
      if (header.hwndFrom == s.link && (header.code == NM_CLICK || header.code == NM_RETURN)) openLicense(s);
      return 0;
    }
    case WM_COMMAND: {
      const int id = LOWORD(wparam);
      if (HIWORD(wparam) != BN_CLICKED) return 0;
      if (id == agreementID) {
        render(s);
      } else if (id == purgeID) {
        InvalidateRect(s.purge, nullptr, FALSE);
      } else if (id == browseID && s.phase == Phase::Ready && s.mode == Mode::Install) {
        chooseDirectory(s);
      } else if (id == detailsID && s.phase != Phase::Ready) {
        s.showDetails = !s.showDetails;
        render(s);
      } else if (id == actionID) {
        if (s.licenseView) closeLicense(s, true);
        else if (s.phase == Phase::Ready) start(s);
        else if (s.phase == Phase::Done && s.mode != Mode::Uninstall) {
          s.result.store(3);
          DestroyWindow(window);
        }
      } else if (id == cancelID) {
        SendMessageW(window, WM_CLOSE, 0, 0);
      }
      return 0;
    }
    case WM_CLOSE:
      if (s.licenseView) { closeLicense(s, false); return 0; }
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
      render(s);
      return 0;
    }
    case WM_THEMECHANGED:
    case WM_SETTINGCHANGE: applyTheme(s); return 0;
    case WM_TIMER:
      if (wparam == spinnerTimer && s.phase == Phase::Working && s.step < stepLimit) InvalidateRect(s.steps[s.step], nullptr, FALSE);
      return 0;
    case updateMessage: {
      std::unique_ptr<std::wstring> line(reinterpret_cast<std::wstring*>(lparam));
      appendLog(s, *line);
      return 0;
    }
    case stepMessage:
      if (s.phase == Phase::Working) {
        s.step = std::clamp(static_cast<int>(wparam), 0, stepLimit - 1);
        render(s);
      }
      return 0;
    case finishMessage: {
      std::unique_ptr<Completion> completion(reinterpret_cast<Completion*>(lparam));
      s.phase = completion->success ? Phase::Done : Phase::Failed;
      if (!completion->success) {
        // The first line is NSIS's own message; anything after it is evidence for the details.
        const std::wstring& detail = completion->detail;
        s.failure = detail.substr(0, detail.find_first_of(L"\r\n"));
        if (s.failure.empty()) s.failure = s.tr(L"操作未完成。", L"The operation didn’t finish.");
      }
      appendLog(s, completion->success && completion->detail.empty() ? s.tr(L"已完成。", L"Done.") : completion->detail);
      render(s);
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
      render(s);
      return 0;
    }
  }
  return DefWindowProcW(window, message, wparam, lparam);
}

bool isButton(const State& s, HWND control) {
  return control == s.primary || control == s.secondary || control == s.detailButton || control == s.browse;
}

void run(State& s) {
  ULONG_PTR gdiplusToken = 0;
  bool com = false;
  Gdiplus::GdiplusStartupInput input;
  try {
    com = SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED));
    if (!com) throw std::runtime_error("installer COM initialization");
    s.license = readLicense(s.assets + L"\\LICENSE.txt");
    using SetDpiContext = HANDLE (WINAPI*)(HANDLE);
    auto setDpi = reinterpret_cast<SetDpiContext>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetThreadDpiAwarenessContext"));
    if (setDpi) setDpi(reinterpret_cast<HANDLE>(static_cast<INT_PTR>(-4))); // per-monitor v2
    if (Gdiplus::GdiplusStartup(&gdiplusToken, &input, nullptr) != Gdiplus::Ok) throw std::runtime_error("GDI+");
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_STANDARD_CLASSES | ICC_LINK_CLASS};
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
    const wchar_t* caption = s.mode == Mode::Uninstall ? s.tr(L"Solaris 卸载程序", L"Solaris Uninstall")
                                                       : s.tr(L"Solaris 安装程序", L"Solaris Setup");
    HWND window = CreateWindowExW(WS_EX_CONTROLPARENT, windowClass, caption, style,
                                  work.left + (work.right - work.left - width) / 2,
                                  work.top + (work.bottom - work.top - height) / 2,
                                  width, height, nullptr, nullptr, module, &s);
    if (!window) throw std::runtime_error("installer window");
    s.started.store(true);
    ShowWindow(window, SW_SHOWNORMAL);
    UpdateWindow(window);
    SetForegroundWindow(window);
    SetFocus(s.mode == Mode::Uninstall ? s.secondary : s.mode == Mode::Upgrade ? s.agreement : s.pathEdit);
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
        if (message.wParam == VK_RETURN) {
          HWND focus = GetFocus();
          if (focus == s.link) { openLicense(s); continue; }
          HWND button = isButton(s, focus) ? focus : s.phase == Phase::Working ? nullptr : defaultButton(s);
          if (button && IsWindowVisible(button) && IsWindowEnabled(button)) SendMessageW(button, BM_CLICK, 0, 0);
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
  for (HFONT f : {s.titleFont, s.bodyFont, s.strongFont, s.captionFont, s.sectionFont, s.labelFont, s.monoFont}) if (f) DeleteObject(f);
  for (HICON i : {s.icon, s.bigIcon, s.smallIcon}) if (i) DestroyIcon(i);
  for (HBRUSH brush : {s.background, s.surface, s.subtle}) if (brush) DeleteObject(brush);
  for (const auto& path : s.loadedFonts) RemoveFontResourceExW(path.c_str(), FR_PRIVATE, nullptr);
  if (gdiplusToken) Gdiplus::GdiplusShutdown(gdiplusToken);
  if (com) CoUninitialize();
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

void postLine(const std::wstring& value) {
  if (!state || !state->window.load() || value.empty()) return;
  auto line = std::make_unique<std::wstring>(value);
  if (PostMessageW(state->window.load(), updateMessage, 0, reinterpret_cast<LPARAM>(line.get()))) line.release();
}
} // namespace

extern "C" void __cdecl Begin(HWND, int size, wchar_t*, stack_t** stack, extra_parameters* extra) {
  Call call{size, stack};
  closeUI();
  try {
    auto mode = call.pop();
    const auto language = call.pop(), version = call.pop();
    const auto directory = call.pop(), assets = call.pop(), theme = call.pop(), architecture = call.pop();
    const auto installed = call.pop();
    if (architecture != L"x64" && architecture != L"arm64") throw std::runtime_error("installer architecture");
    state = std::make_unique<State>();
    if (mode.rfind(L"preview:", 0) == 0) { state->preview = true; mode.erase(0, 8); }
    state->mode = mode == L"upgrade" ? Mode::Upgrade
                : mode == L"uninstall" || mode == L"uninstall-purge" ? Mode::Uninstall : Mode::Install;
    state->purgeDefault = mode == L"uninstall-purge";
    state->chinese = language == L"2052";
    state->version = version;
    // Shown only: the registered DisplayVersion of the installation being updated.
    if (installed.size() <= 32) state->installed = installed;
    state->architecture = architecture == L"arm64" ? L"ARM64" : L"x64";
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

// Status and Log both add a line to the details; the step list follows Step alone.
extern "C" void __cdecl Status(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack}; postLine(call.pop());
}
extern "C" void __cdecl Log(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack}; postLine(call.pop());
}
extern "C" void __cdecl Step(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack};
  const auto index = call.pop();
  if (!state || !state->window.load() || index.size() != 1 || index[0] < L'0' || index[0] >= L'0' + stepLimit) return;
  PostMessageW(state->window.load(), stepMessage, static_cast<WPARAM>(index[0] - L'0'), 0);
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
extern "C" void __cdecl GetDirectory(HWND, int size, wchar_t*, stack_t** stack, extra_parameters*) {
  Call call{size, stack};
  call.push(state ? state->directory.c_str() : L"");
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) module = instance;
  return TRUE;
}
