// Virtual-resolution UI scaling for CivilizationV_DX11.exe.
//
// The engine keeps the screen size in the global UI/app object (+0x644/+0x648).
// The resolution-apply routine (called at startup and on every resolution
// change) writes the real client size there, hands it to the device/swapchain
// reset, and *afterwards* builds the UI ortho projection (+0x5f4) and the
// world-anchor viewport from the same fields. We let it run untouched, then
// replace the fields with size/scale and rebuild only the UI projection. All UI
// layout, Lua (UIManager:GetScreenSizeVal) and world anchors then work in
// virtual units while the swapchain and 3D renderer keep the real size.
// Mouse messages are divided by the scale so hit-testing matches.
#include "core.h"

#include <windows.h>

#include <string>
#include <windowsx.h>

#include <cmath>

#include "config.h"
#include "hook.h"
#include "log.h"

namespace uis {
void HookD3DVtablesOnce();
void VerifyD3DHooks();
void EnsureD3DHooks();
bool D3DHooksReady();

namespace {
constexpr int kOffW = 0x644;
constexpr int kOffH = 0x648;

struct Addrs {
  uintptr_t applyRes = 0;      // void __thiscall ApplyResolution(char)
  uintptr_t ppUiObj = 0;       // address of global pointer to UI/app object
  uintptr_t rebuildOrtho = 0;  // void __thiscall RebuildUIProjection()
  uintptr_t autoSmallFlag = 0; // bool: "auto" small UI assets
  uintptr_t smallA = 0;        // bool: small UI assets (two copies)
  uintptr_t smallB = 0;
  // Return addresses of the first Win32 call ApplyResolution makes *after* the
  // device/swapchain reset (fullscreen and windowed paths).
  uintptr_t retAfterResetFS = 0;   // SetWindowPos
  uintptr_t retAfterResetWin = 0;  // SetActiveWindow
} A;

using ApplyResFn = void(__fastcall*)(void* self, void* edx, int arg);

using SetWindowPosFn = BOOL(WINAPI*)(HWND, HWND, int, int, int, int, UINT);
using SetActiveWindowFn = HWND(WINAPI*)(HWND);
SetWindowPosFn g_origSetWindowPos = nullptr;
SetActiveWindowFn g_origSetActiveWindow = nullptr;

using PeekMessageWFn = BOOL(WINAPI*)(LPMSG, HWND, UINT, UINT, UINT);
PeekMessageWFn g_origPeek = nullptr;

WNDPROC g_origWndProc = nullptr;
HWND g_gameHwnd = nullptr;
// Last mouse message we scaled in PeekMessageW, so the window procedure does not
// scale it a second time when it is dispatched.
UINT g_lastScaledMsg = 0;
LPARAM g_lastScaledLParam = 0;

bool g_active = false;
int g_realW = 0, g_realH = 0;
int g_virtW = 0, g_virtH = 0;
float g_scale = 1.0f;  // scale currently applied to the UI
bool g_inApply = false;

uint8_t* UiObj() { return A.ppUiObj ? *reinterpret_cast<uint8_t**>(A.ppUiObj) : nullptr; }

float Clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

float ComputeScale() {
  float s = g_cfg.scale > 0.0f ? g_cfg.scale : AutoScale();
  return Clamp(s, g_cfg.minScale, g_cfg.maxScale);
}

// The engine picks "small UI assets" automatically for heights < 1100 from the
// *real* renderer height (in two places). Layout happens at the virtual height,
// so re-evaluate it from that. Pure data writes, called every frame.
void EnforceSmallAssets() {
  if (!A.autoSmallFlag || g_scale == 1.0f || g_virtH <= 0) return;
  if (!*reinterpret_cast<uint8_t*>(A.autoSmallFlag)) return;
  uint8_t small = g_virtH < 1100 ? 1 : 0;
  auto a = reinterpret_cast<uint8_t*>(A.smallA);
  auto b = reinterpret_cast<uint8_t*>(A.smallB);
  if (*a != small) *a = small;
  if (*b != small) *b = small;
}

// Called inside ApplyResolution right after the device reset, i.e. after the
// real size was consumed by the swapchain but before the UI projection, the
// world-anchor viewport and the re-anchor request are built from it.
// Safe mode: without the D3D hooks a scaled UI renders with empty lists and text fields
// (clip shaders unpatched), so the UI then stays at 100 % for the whole session and the
// player is told why.
bool g_safeMode = false;
std::string g_warning;

DWORD WINAPI WarningBox(LPVOID) {
  std::wstring w(g_warning.begin(), g_warning.end());
  MessageBoxW(nullptr, w.c_str(), L"Civ V UI Scale",
              MB_OK | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND);
  return 0;
}

void CheckSafeMode() {
  static bool checked = false;
  if (checked) return;
  checked = true;
  EnsureD3DHooks();
  if (D3DHooksReady() || ComputeScale() < 1.01f) return;
  g_safeMode = true;
  g_warning =
      "Civ V UI Scale could not attach to the game's graphics this time, so the interface "
      "stays at its normal size for this session. Everything else works as usual.\n\n"
      "Restarting the game usually fixes this. If it keeps happening, please report it and "
      "include Logs\\UIScale.log from My Games\\Sid Meier's Civilization 5.";
  Log("SAFE MODE: D3D hooks not installed before the UI layout - UI scaling disabled for "
      "this session");
  if (g_cfg.failWarning) CloseHandle(CreateThread(nullptr, 0, WarningBox, nullptr, 0, nullptr));
}

void ConvertToVirtual(uint8_t* obj) {
  g_realW = *reinterpret_cast<int*>(obj + kOffW);
  g_realH = *reinterpret_cast<int*>(obj + kOffH);
  CheckSafeMode();
  g_scale = g_safeMode ? 1.0f : ComputeScale();
  g_virtW = (int)lroundf(g_realW / g_scale);
  g_virtH = (int)lroundf(g_realH / g_scale);
  if (g_virtW < 800 || g_virtH < 600) {  // keep layouts above a sane minimum size
    float s = fminf((float)g_realW / 800.0f, (float)g_realH / 600.0f);
    g_scale = fmaxf(1.0f, s);
    g_virtW = (int)lroundf(g_realW / g_scale);
    g_virtH = (int)lroundf(g_realH / g_scale);
  }
  Log("ApplyResolution: real %dx%d  scale %.3f  virtual %dx%d", g_realW, g_realH, g_scale,
      g_virtW, g_virtH);
  if (g_virtW == g_realW && g_virtH == g_realH) return;

  *reinterpret_cast<int*>(obj + kOffW) = g_virtW;
  *reinterpret_cast<int*>(obj + kOffH) = g_virtH;

  EnforceSmallAssets();
}

bool IsScaledMouseMsg(UINT m) {
  return (m >= WM_MOUSEMOVE && m <= WM_MBUTTONDBLCLK) ||
         (m >= WM_XBUTTONDOWN && m <= WM_XBUTTONDBLCLK) || m == WM_MOUSEHOVER;
}

// The game re-posts WM_MOUSEMOVE with its stored (already virtual) position to
// refresh hover state. Such self-generated mouse messages are tagged with this
// wParam bit (unused by MK_* flags) so they are not scaled a second time.
constexpr WPARAM kSelfPosted = 0x40000000;

using PostMessageAFn = BOOL(WINAPI*)(HWND, UINT, WPARAM, LPARAM);
using SendMessageAFn = LRESULT(WINAPI*)(HWND, UINT, WPARAM, LPARAM);
PostMessageAFn g_origPostMessageA = nullptr;
SendMessageAFn g_origSendMessageA = nullptr;

BOOL WINAPI HookPostMessageA(HWND h, UINT m, WPARAM w, LPARAM l) {
  if (g_active && IsScaledMouseMsg(m)) w |= kSelfPosted;
  return g_origPostMessageA(h, m, w, l);
}

LRESULT WINAPI HookSendMessageA(HWND h, UINT m, WPARAM w, LPARAM l) {
  if (g_active && IsScaledMouseMsg(m)) w |= kSelfPosted;
  return g_origSendMessageA(h, m, w, l);
}

LPARAM ScaleLParam(LPARAM lp) {
  int vx = (int)floorf(GET_X_LPARAM(lp) / g_scale);
  int vy = (int)floorf(GET_Y_LPARAM(lp) / g_scale);
  return MAKELPARAM((WORD)(short)vx, (WORD)(short)vy);
}

// Mouse messages that Wine sends directly to the window (not via the queue)
// never pass through PeekMessageW; scale those here.
float g_lastLoggedX = -1, g_lastLoggedY = -1;
void TracePos(const char* where, UINT m, LPARAM l) {
  if (!g_cfg.logMouse) return;
  uint8_t* obj = UiObj();
  if (!obj) return;
  float x = *reinterpret_cast<float*>(obj + 0x654), y = *reinterpret_cast<float*>(obj + 0x658);
  if (x == g_lastLoggedX && y == g_lastLoggedY) return;
  g_lastLoggedX = x;
  g_lastLoggedY = y;
  Log("pos -> %.0f,%.0f  [%s msg 0x%x lp %d,%d]", x, y, where, m, GET_X_LPARAM(l), GET_Y_LPARAM(l));
}

// Screenshot mouse lock (UIScale.LockMouse, only with Shots=<run>): real mouse input is
// ignored and the game's cursor stays parked at a fixed client position, so a pointer
// that left the window cannot edge-scroll the map or open tooltips between shots.
bool g_mouseLock = false;
float g_parkFx = 0.98f, g_parkFy = 0.97f;  // fraction of the client area

bool ParkPoint(POINT* client) {
  RECT rc;
  if (!g_gameHwnd || !GetClientRect(g_gameHwnd, &rc) || rc.right <= 0) return false;
  client->x = (LONG)(rc.right * g_parkFx);
  client->y = (LONG)(rc.bottom * g_parkFy);
  return true;
}

// Called every frame while locked: the game's stored cursor (UI units) = park point.
void EnforceMouseLock() {
  // The lock lasts only while tools/shots.py runs: it restores UIScale.ini (no Shots=)
  // when it finishes, fails or is interrupted.
  static DWORD lastCheck = 0;
  DWORD now = GetTickCount();
  if (now - lastCheck > 1000) {
    lastCheck = now;
    if (!ShotsStillRequested()) {
      g_mouseLock = false;
      Log("screenshot mouse lock off (Shots= no longer set)");
      return;
    }
  }
  POINT p;
  uint8_t* obj = UiObj();
  if (!g_mouseLock || !obj || !ParkPoint(&p)) return;
  float s = g_scale > 0 ? g_scale : 1.0f;
  *reinterpret_cast<float*>(obj + 0x654) = floorf(p.x / s);
  *reinterpret_cast<float*>(obj + 0x658) = floorf(p.y / s);
}

using GetCursorInfoFn = BOOL(WINAPI*)(PCURSORINFO);
GetCursorInfoFn g_origGetCursorInfo = nullptr;
BOOL WINAPI HookGetCursorInfo(PCURSORINFO ci) {
  BOOL r = g_origGetCursorInfo(ci);
  POINT p;
  if (r && ci && g_mouseLock && ParkPoint(&p)) {
    ClientToScreen(g_gameHwnd, &p);
    ci->ptScreenPos = p;
  }
  return r;
}

LRESULT CALLBACK SubclassWndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
  if (g_mouseLock && IsScaledMouseMsg(m) && !(w & kSelfPosted)) return 0;  // real mouse
  if (IsScaledMouseMsg(m) && (w & kSelfPosted)) {
    w &= ~kSelfPosted;  // game-generated, already in UI coordinates
  } else if (g_active && g_scale != 1.0f && IsScaledMouseMsg(m)) {
    if (m == g_lastScaledMsg && l == g_lastScaledLParam) {
      g_lastScaledMsg = 0;  // dispatched from the queue: already scaled
    } else {
      LPARAM v = ScaleLParam(l);
      if (g_cfg.logMouse)
        Log("wndproc direct msg 0x%x  %d,%d -> %d,%d", m, GET_X_LPARAM(l), GET_Y_LPARAM(l),
            GET_X_LPARAM(v), GET_Y_LPARAM(v));
      l = v;
    }
  }
  LRESULT r = CallWindowProcA(g_origWndProc, h, m, w, l);
  TracePos("wndproc", m, l);
  return r;
}

void SubclassGameWindow(HWND h) {
  if (!h || g_gameHwnd == h) return;
  g_gameHwnd = h;
  g_origWndProc = reinterpret_cast<WNDPROC>(
      SetWindowLongA(h, GWL_WNDPROC, reinterpret_cast<LONG>(&SubclassWndProc)));
  Log("Subclassed game window %p (orig wndproc %p)", (void*)h, (void*)g_origWndProc);
}

void AfterResetHook(uintptr_t ret, HWND h) {
  if (!g_active) return;
  if (ret != A.retAfterResetFS && ret != A.retAfterResetWin) return;
  SubclassGameWindow(h);
  if (uint8_t* obj = UiObj()) ConvertToVirtual(obj);
}

BOOL WINAPI HookSetWindowPos(HWND h, HWND after, int x, int y, int cx, int cy, UINT flags) {
  uintptr_t ret = (uintptr_t)__builtin_return_address(0);
  BOOL r = g_origSetWindowPos(h, after, x, y, cx, cy, flags);
  AfterResetHook(ret, h);
  return r;
}

HWND WINAPI HookSetActiveWindow(HWND h) {
  uintptr_t ret = (uintptr_t)__builtin_return_address(0);
  HWND r = g_origSetActiveWindow(h);
  AfterResetHook(ret, h);
  return r;
}

BOOL WINAPI HookPeekMessageW(LPMSG msg, HWND hwnd, UINT fmin, UINT fmax, UINT remove) {
  if (g_active) {
    TracePos("frame", 0, 0);
    HookD3DVtablesOnce();
    VerifyD3DHooks();
  }  // change since the last message => not message-driven
  if (g_mouseLock) EnforceMouseLock();
  BOOL r = g_origPeek(msg, hwnd, fmin, fmax, remove);
  if (g_active) EnforceSmallAssets();
  if (r && g_mouseLock && IsScaledMouseMsg(msg->message) && !(msg->wParam & kSelfPosted)) {
    msg->message = WM_NULL;  // real mouse input while the screenshot driver runs
    return r;
  }
  if (r && IsScaledMouseMsg(msg->message) && (msg->wParam & kSelfPosted)) {
    // Game-generated: already in UI coordinates. Strip the tag and tell the
    // window procedure not to scale it when it is dispatched.
    msg->wParam &= ~kSelfPosted;
    if (remove & PM_REMOVE) {
      g_lastScaledMsg = msg->message;
      g_lastScaledLParam = msg->lParam;
    }
    return r;
  }
  if (r && g_active && g_scale != 1.0f && IsScaledMouseMsg(msg->message)) {
    LPARAM orig = msg->lParam;
    msg->lParam = ScaleLParam(orig);
    if (remove & PM_REMOVE) {
      g_lastScaledMsg = msg->message;
      g_lastScaledLParam = msg->lParam;
    }
    if (g_cfg.logMouse && msg->message != WM_MOUSEMOVE)
      Log("peek msg 0x%x  %d,%d -> %d,%d", msg->message, GET_X_LPARAM(orig), GET_Y_LPARAM(orig),
          GET_X_LPARAM(msg->lParam), GET_Y_LPARAM(msg->lParam));
  }
  return r;
}

uintptr_t Find(HMODULE exe, const char* name, const char* sig) {
  int n = 0;
  uintptr_t a = SigScan(exe, sig, &n);
  Log("sig %-16s -> %s 0x%08x (%d matches)", name, a ? "OK " : "MISS", (unsigned)a, n);
  return a;
}
}  // namespace

bool InstallCore() {
  HMODULE exe = GetModuleHandleW(nullptr);

  A.applyRes = Find(exe, "ApplyResolution",
                    "81 EC 7C 03 00 00 80 3D ?? ?? ?? ?? 00 A1 ?? ?? ?? ?? 8B 15 ?? ?? ?? ?? 53 "
                    "8B 98 A4 00 00 00");
  uintptr_t ssv = Find(exe, "GetScreenSizeVal",
                       "A1 ?? ?? ?? ?? 8B 88 44 06 00 00 56 8B 74 24 08 51 56 E8");
  uintptr_t fs = Find(exe, "PostResetFS",
                      "E8 ?? ?? ?? ?? 85 C0 75 ?? 50 57 56 8B 35 ?? ?? ?? ?? 50 50 50 53 FF D6 F6 85 "
                      "EC 06 00 00 02");
  uintptr_t win = Find(exe, "PostResetWin",
                       "51 8B 0D ?? ?? ?? ?? 50 E8 ?? ?? ?? ?? 53 FF 15 ?? ?? ?? ?? 80 3D ?? ?? ?? ?? "
                       "00 74 17 81 3D");
  uintptr_t small = Find(exe, "AutoSmallAssets",
                         "80 3D ?? ?? ?? ?? 00 74 17 81 3D ?? ?? ?? ?? 4C 04 00 00 0F 92 C0 A2 ?? "
                         "?? ?? ?? A2 ?? ?? ?? ?? 33 C0");
  if (ssv) A.ppUiObj = Read<uint32_t>(ssv + 1);
  if (fs) A.retAfterResetFS = fs + 24;
  if (win) A.retAfterResetWin = win + 20;
  if (small) {
    A.autoSmallFlag = Read<uint32_t>(small + 2);
    A.smallA = Read<uint32_t>(small + 23);
    A.smallB = Read<uint32_t>(small + 28);
  }

  if (!A.applyRes || !A.ppUiObj || !fs || !win) {
    Log("Required signatures missing - UI scaling disabled (unsupported exe build?)");
    return false;
  }

  // Import-table hooks only: the exe is Steam-CEG protected and must not have
  // its code modified.
  g_origSetWindowPos = reinterpret_cast<SetWindowPosFn>(
      PatchIAT(exe, "USER32.dll", "SetWindowPos", (void*)&HookSetWindowPos));
  g_origSetActiveWindow = reinterpret_cast<SetActiveWindowFn>(
      PatchIAT(exe, "USER32.dll", "SetActiveWindow", (void*)&HookSetActiveWindow));
  g_origPostMessageA = reinterpret_cast<PostMessageAFn>(
      PatchIAT(exe, "USER32.dll", "PostMessageA", (void*)&HookPostMessageA));
  g_origSendMessageA = reinterpret_cast<SendMessageAFn>(
      PatchIAT(exe, "USER32.dll", "SendMessageA", (void*)&HookSendMessageA));
  Log("PostMessageA hook %s, SendMessageA hook %s", g_origPostMessageA ? "ok" : "FAILED",
      g_origSendMessageA ? "ok" : "FAILED");
  g_origPeek = reinterpret_cast<PeekMessageWFn>(
      PatchIAT(exe, "USER32.dll", "PeekMessageW", (void*)&HookPeekMessageW));
  if (!g_origSetWindowPos || !g_origSetActiveWindow || !g_origPeek) {
    Log("IAT hooks failed (SetWindowPos %p, SetActiveWindow %p, PeekMessageW %p) - disabled",
        (void*)g_origSetWindowPos, (void*)g_origSetActiveWindow, (void*)g_origPeek);
    return false;
  }
  g_active = true;
  if (!g_cfg.shots.empty()) {
    g_origGetCursorInfo = reinterpret_cast<GetCursorInfoFn>(
        PatchIAT(exe, "USER32.dll", "GetCursorInfo", (void*)&HookGetCursorInfo));
    swscanf(g_cfg.shotMouse.c_str(), L"%f,%f", &g_parkFx, &g_parkFy);
  }
  Log("Core hooks installed (setting %.2f, 0 = auto)", g_cfg.scale);
  return true;
}

bool IsActive() { return g_active; }
const char* Warning() { return g_warning.empty() ? nullptr : g_warning.c_str(); }
float EffectiveScale() { return g_scale; }
float ConfiguredScale() { return ComputeScale(); }

float AutoScale() {
  int h = g_realH;
  if (h <= 0) h = GetSystemMetrics(SM_CYSCREEN);
  return fmaxf(1.0f, h / 1080.0f);
}

void GetRealSize(int* w, int* h) {
  *w = g_realW;
  *h = g_realH;
}
void GetVirtualSize(int* w, int* h) {
  *w = g_virtW;
  *h = g_virtH;
}

void LockMouseForShots() {
  if (g_cfg.shots.empty() || !g_active) return;
  g_mouseLock = true;
  POINT p;
  bool ok = ParkPoint(&p);
  Log("screenshot mouse lock on: parked at %ld,%ld (GetCursorInfo hook %s)", ok ? p.x : -1L,
      ok ? p.y : -1L, g_origGetCursorInfo ? "ok" : "missing");
  if (ok) {
    float s = g_scale > 0 ? g_scale : 1.0f;
    int vx = (int)floorf(p.x / s), vy = (int)floorf(p.y / s);
    // tagged as game-generated: already in UI units, passes the lock
    PostMessageA(g_gameHwnd, WM_MOUSEMOVE, kSelfPosted, MAKELPARAM(vx, vy));
  }
}

void UnlockMouseForShots() {
  if (g_mouseLock) Log("screenshot mouse lock off");
  g_mouseLock = false;
}

void SetScaleSetting(float setting, bool apply) {
  g_cfg.scale = setting <= 0.0f ? 0.0f : Clamp(setting, g_cfg.minScale, g_cfg.maxScale);
  SaveConfig();
  Log("Scale setting -> %.2f (apply=%d)", g_cfg.scale, apply);
  if (!apply || !g_active || g_inApply) return;
  uint8_t* obj = UiObj();
  if (!obj) return;
  // Re-run the engine's resolution apply at the unchanged real resolution; our
  // post-reset hook converts to the new virtual size and the engine rebuilds the
  // UI projection and requests re-anchoring of all contexts.
  g_inApply = true;
  reinterpret_cast<ApplyResFn>(A.applyRes)(obj, nullptr, 1);
  g_inApply = false;
}
}  // namespace uis
