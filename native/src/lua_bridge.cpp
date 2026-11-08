// Exposes the mod to Lua:
//   UIScale.IsActive()        -> bool   (false if the exe build is unsupported)
//   UIScale.GetWarning()      -> string or nil (safe mode: why the UI is not scaled)
//   UIScale.Get()             -> number (scale currently applied)
//   UIScale.GetSetting()      -> number (configured value, 0 = auto)
//   UIScale.GetAuto()         -> number (what "auto" resolves to)
//   UIScale.GetRealSize()     -> w, h
//   UIScale.GetVirtualSize()  -> w, h
//   UIScale.Set(n [, apply])  -> stores the setting (0 = auto); applies live unless apply == false
// Screenshot automation (tools/shots.py; only with Shots=<run> in UIScale.ini):
//   UIScale.ShotConfig()      -> run, save path, scales string, camera target (nil if off)
//   UIScale.Shot(name)        -> logs "SHOT <name>"; the host script captures the screen
//   UIScale.ShotsDone()       -> logs "SHOTS DONE"
//   UIScale.PostKey(vk)       -> key press posted to the game window (e.g. 27 = Escape)
//   UIScale.ShotNote(text)    -> logs "SHOTNOTE <text>" (shown to the user by shots.py)
//   UIScale.LockMouse()       -> ignore the real mouse, park the cursor (core.h)
//   UIScale.UnlockMouse()     -> real mouse back (also automatic when Shots= is removed)
#include "lua_bridge.h"

#include <windows.h>

#include <cstring>
#include <string>

#include "config.h"
#include "core.h"
#include "hook.h"
#include "log.h"

namespace uis {
namespace {
struct lua_State;
using lua_CFunction = int(__cdecl*)(lua_State*);
constexpr int LUA_GLOBALSINDEX = -10002;

struct LuaApi {
  void(__cdecl* pushcclosure)(lua_State*, lua_CFunction, int);
  void(__cdecl* setfield)(lua_State*, int, const char*);
  void(__cdecl* createtable)(lua_State*, int, int);
  void(__cdecl* pushnumber)(lua_State*, double);
  void(__cdecl* pushboolean)(lua_State*, int);
  double(__cdecl* tonumber)(lua_State*, int);
  int(__cdecl* toboolean)(lua_State*, int);
  int(__cdecl* gettop)(lua_State*);
  int(__cdecl* type)(lua_State*, int);
  void(__cdecl* pushstring)(lua_State*, const char*);
  void(__cdecl* rawset)(lua_State*, int);
  const char*(__cdecl* tolstring)(lua_State*, int, size_t*);
} L_;

using OpenLibsFn = void(__cdecl*)(lua_State*);
OpenLibsFn g_origOpenLibs = nullptr;
using SetFieldFn = void(__cdecl*)(lua_State*, int, const char*);
SetFieldFn g_origSetField = nullptr;
using SetFenvFn = int(__cdecl*)(lua_State*, int);
SetFenvFn g_origSetFenv = nullptr;
constexpr int LUA_REGISTRYINDEX = -10000;

int __cdecl l_GetWarning(lua_State* L) {
  const char* w = Warning();
  if (!w) return 0;
  L_.pushstring(L, w);
  return 1;
}

int __cdecl l_IsActive(lua_State* L) {
  L_.pushboolean(L, IsActive());
  return 1;
}
int __cdecl l_Get(lua_State* L) {
  L_.pushnumber(L, EffectiveScale());
  return 1;
}
int __cdecl l_GetSetting(lua_State* L) {
  L_.pushnumber(L, g_cfg.scale);
  return 1;
}
int __cdecl l_GetAuto(lua_State* L) {
  L_.pushnumber(L, AutoScale());
  return 1;
}
int __cdecl l_GetRealSize(lua_State* L) {
  int w, h;
  GetRealSize(&w, &h);
  L_.pushnumber(L, w);
  L_.pushnumber(L, h);
  return 2;
}
int __cdecl l_GetVirtualSize(lua_State* L) {
  int w, h;
  GetVirtualSize(&w, &h);
  L_.pushnumber(L, w);
  L_.pushnumber(L, h);
  return 2;
}
int __cdecl l_Set(lua_State* L) {
  // Accept both UIScale.Set(n) and UIScale:Set(n).
  int top = L_.gettop(L);
  int idx = 1;
  if (top >= 1 && L_.type(L, 1) == 5 /*LUA_TTABLE*/) idx = 2;
  double v = L_.tonumber(L, idx);
  bool apply = true;
  if (top >= idx + 1 && L_.type(L, idx + 1) != 0 /*nil*/) apply = L_.toboolean(L, idx + 1) != 0;
  SetScaleSetting((float)v, apply);
  return 0;
}

std::string Utf8(const std::wstring& w) {
  char buf[1024];
  int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, buf, sizeof buf, nullptr, nullptr);
  return n > 0 ? buf : "";
}

// First string argument (UIScale.F(x) or UIScale:F(x)).
const char* StrArg(lua_State* L) {
  int idx = L_.gettop(L) >= 2 && L_.type(L, 1) == 5 /*LUA_TTABLE*/ ? 2 : 1;
  return L_.tolstring ? L_.tolstring(L, idx, nullptr) : nullptr;
}

int __cdecl l_ShotConfig(lua_State* L) {
  if (g_cfg.shots.empty()) return 0;
  std::wstring save = g_cfg.shotSave;
  if (!save.empty() && save.find(L'\\') == std::wstring::npos && save.find(L':') == std::wstring::npos) {
    save = g_userDir + L"\\Saves\\single\\" + save;
    if (save.size() < 10 || _wcsicmp(save.c_str() + save.size() - 10, L".Civ5Save") != 0)
      save += L".Civ5Save";
  }
  L_.pushstring(L, Utf8(g_cfg.shots).c_str());
  L_.pushstring(L, Utf8(save).c_str());
  L_.pushstring(L, Utf8(g_cfg.shotScales.empty() ? L"0" : g_cfg.shotScales).c_str());
  L_.pushstring(L, Utf8(g_cfg.shotLook).c_str());
  return 4;
}

int __cdecl l_Shot(lua_State* L) {
  const char* n = StrArg(L);
  if (!g_cfg.shots.empty() && n) Log("SHOT %s", n);
  return 0;
}

int __cdecl l_ShotNote(lua_State* L) {
  const char* n = StrArg(L);
  if (!g_cfg.shots.empty() && n) Log("SHOTNOTE %s", n);
  return 0;
}

int __cdecl l_LockMouse(lua_State* L) {
  LockMouseForShots();
  return 0;
}

int __cdecl l_UnlockMouse(lua_State* L) {
  UnlockMouseForShots();
  return 0;
}

int __cdecl l_ShotsDone(lua_State* L) {
  if (!g_cfg.shots.empty()) Log("SHOTS DONE");
  return 0;
}

BOOL CALLBACK FindGameWindow(HWND h, LPARAM out) {
  DWORD pid = 0;
  GetWindowThreadProcessId(h, &pid);
  if (pid != GetCurrentProcessId() || !IsWindowVisible(h)) return TRUE;
  RECT r, best;
  GetWindowRect(h, &r);
  HWND* o = reinterpret_cast<HWND*>(out);
  if (*o) GetWindowRect(*o, &best);
  if (!*o || (r.right - r.left) * (r.bottom - r.top) > (best.right - best.left) * (best.bottom - best.top))
    *o = h;
  return TRUE;
}

int __cdecl l_PostKey(lua_State* L) {
  if (g_cfg.shots.empty()) return 0;
  int idx = L_.gettop(L) >= 2 && L_.type(L, 1) == 5 ? 2 : 1;
  WPARAM vk = (WPARAM)L_.tonumber(L, idx);
  HWND h = nullptr;
  EnumWindows(FindGameWindow, reinterpret_cast<LPARAM>(&h));
  if (h && vk) {
    UINT sc = MapVirtualKeyW((UINT)vk, 0 /*MAPVK_VK_TO_VSC*/);
    PostMessageW(h, WM_KEYDOWN, vk, 1 | (sc << 16));
    PostMessageW(h, WM_KEYUP, vk, 1 | (sc << 16) | (1u << 30) | (1u << 31));
  }
  return 0;
}

// Pushes a new UIScale table onto the stack.
void PushTable(lua_State* L) {
  L_.createtable(L, 0, 15);
  struct {
    const char* name;
    lua_CFunction fn;
  } fns[] = {{"IsActive", l_IsActive},       {"Get", l_Get},
             {"GetSetting", l_GetSetting},   {"GetAuto", l_GetAuto},
             {"GetRealSize", l_GetRealSize}, {"GetVirtualSize", l_GetVirtualSize},
             {"Set", l_Set},                 {"GetWarning", l_GetWarning},
             {"ShotConfig", l_ShotConfig},   {"Shot", l_Shot},
             {"ShotsDone", l_ShotsDone},     {"PostKey", l_PostKey},
             {"ShotNote", l_ShotNote},       {"LockMouse", l_LockMouse},
             {"UnlockMouse", l_UnlockMouse}};
  for (auto& f : fns) {
    L_.pushcclosure(L, f.fn, 0);
    L_.setfield(L, -2, f.name);
  }
}

void Register(lua_State* L) {
  PushTable(L);
  g_origSetField(L, LUA_GLOBALSINDEX, "UIScale");
}

// UI contexts run in their own environment tables. Whenever the game installs
// its UIManager API into a table, put UIScale next to it.
void __cdecl HookSetField(lua_State* L, int idx, const char* k) {
  int absIdx = idx;
  if (idx < 0 && idx > LUA_REGISTRYINDEX) absIdx = L_.gettop(L) + idx + 1;  // before the pop
  g_origSetField(L, idx, k);
  if (k && k[0] == 'U' && strcmp(k, "UIManager") == 0) {
    if (idx < 0 && idx > LUA_REGISTRYINDEX) {
      // Table index was relative to the stack top before the value was popped.
      if (absIdx > L_.gettop(L)) return;
    }
    PushTable(L);
    g_origSetField(L, absIdx, "UIScale");
    static int n = 0;
    if (++n <= 3 || n % 50 == 0) Log("UIScale injected next to UIManager (#%d, idx %d)", n, idx);
  }
}

// Each UI context (XML + Lua file) gets its own environment table, installed
// with lua_setfenv right before its chunk runs. Add UIScale to it.
int __cdecl HookSetFenv(lua_State* L, int idx) {
  if (L_.type(L, -1) == 5 /*LUA_TTABLE*/) {
    L_.pushstring(L, "UIScale");
    PushTable(L);
    L_.rawset(L, -3);
    static int n = 0;
    if (++n <= 3) Log("UIScale added to a context environment (#%d)", n);
  }
  return g_origSetFenv(L, idx);
}

void __cdecl HookOpenLibs(lua_State* L) {
  g_origOpenLibs(L);
  if (g_origSetField) Register(L);
}
}  // namespace

bool InstallLuaBridge() {
  HMODULE lua = GetModuleHandleA("lua51_Win32.dll");
  if (!lua) {
    Log("lua51_Win32.dll not loaded - no Lua bridge");
    return false;
  }
#define R(field, name) *(FARPROC*)&L_.field = GetProcAddress(lua, name)
  R(pushcclosure, "lua_pushcclosure");
  R(setfield, "lua_setfield");
  R(createtable, "lua_createtable");
  R(pushnumber, "lua_pushnumber");
  R(pushboolean, "lua_pushboolean");
  R(tonumber, "lua_tonumber");
  R(toboolean, "lua_toboolean");
  R(gettop, "lua_gettop");
  R(type, "lua_type");
  R(pushstring, "lua_pushstring");
  R(rawset, "lua_rawset");
  R(tolstring, "lua_tolstring");  // optional (screenshot automation only)
#undef R
  if (!L_.pushcclosure || !L_.setfield || !L_.createtable || !L_.pushnumber ||
      !L_.pushboolean || !L_.tonumber || !L_.toboolean || !L_.gettop || !L_.type || !L_.pushstring || !L_.rawset) {
    Log("Lua API exports missing - no Lua bridge");
    return false;
  }
  g_origSetField = reinterpret_cast<SetFieldFn>(PatchIAT(
      GetModuleHandleW(nullptr), "lua51_Win32.dll", "lua_setfield", (void*)&HookSetField));
  if (!g_origSetField) g_origSetField = L_.setfield;
  g_origSetFenv = reinterpret_cast<SetFenvFn>(PatchIAT(
      GetModuleHandleW(nullptr), "lua51_Win32.dll", "lua_setfenv", (void*)&HookSetFenv));
  Log("lua_setfenv hook %s", g_origSetFenv ? "ok" : "FAILED");
  g_origOpenLibs = reinterpret_cast<OpenLibsFn>(
      PatchIAT(GetModuleHandleW(nullptr), "lua51_Win32.dll", "luaL_openlibs",
               (void*)&HookOpenLibs));
  Log("Lua bridge %s", g_origOpenLibs ? "installed" : "FAILED (luaL_openlibs import not found)");
  return g_origOpenLibs != nullptr;
}
}  // namespace uis
