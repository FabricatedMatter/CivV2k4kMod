#include <windows.h>
#include <shlobj.h>

#include <string>
#include <vector>

#include "config.h"
#include "core.h"
#include "log.h"
#include "lua_bridge.h"
#include "optionspatch.h"
#include "redirect.h"
#include "version.h"

namespace uis {
bool LoadRealVersionDll();
void InstallCrashLogger();
void InstallD3DHooks();
void InstallStrategicFix(DWORD mainThreadId);
}

using namespace uis;

static std::wstring ExeDir(std::wstring* exeName) {
  wchar_t buf[MAX_PATH];
  GetModuleFileNameW(nullptr, buf, MAX_PATH);
  std::wstring p = buf;
  size_t slash = p.find_last_of(L"\\/");
  if (exeName) *exeName = p.substr(slash + 1);
  return p.substr(0, slash);
}

static std::wstring UserDir() {
  wchar_t docs[MAX_PATH];
  if (FAILED(SHGetFolderPathW(nullptr, CSIDL_PERSONAL, nullptr, 0, docs))) return L".";
  return std::wstring(docs) + L"\\My Games\\Sid Meier's Civilization 5";
}

static void Init() {
  std::wstring exeName;
  std::wstring gameDir = ExeDir(&exeName);
  if (_wcsicmp(exeName.c_str(), L"CivilizationV_DX11.exe") != 0) return;  // inert elsewhere

  g_userDir = UserDir();
  g_modDir = gameDir + L"\\UIScale";
  CreateDirectoryW((g_userDir + L"\\Logs").c_str(), nullptr);
  LogInit(g_userDir + L"\\Logs\\UIScale.log");
  Log("Civ V UI Scale " UISCALE_VER_STR " loaded into %ls", exeName.c_str());
  {
    wchar_t self[MAX_PATH] = L"?";
    HMODULE me = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)&ExeDir, &me);
    if (me) GetModuleFileNameW(me, self, MAX_PATH);
    Log("mod DLL: %ls", self);
  }

  InstallCrashLogger();
  LoadConfig();
  bool core = InstallCore();
  InstallLuaBridge();  // also when inactive, so the options UI can say so
  if (core) {
    std::vector<std::wstring> roots = {g_modDir + L"\\Override"};
    // Hi-res font atlases (tools/fontgen.py): 2x up to 250 %, 3x above.
    float s = ConfiguredScale();
    if (g_cfg.crispFonts && s >= 1.25f) {
      // 3x only if installed (releases ship 2x, which is also fine above 250 %)
      const wchar_t* k = s > 2.5f && GetFileAttributesW((g_modDir + L"\\Fonts\\x3").c_str()) !=
                                         INVALID_FILE_ATTRIBUTES ? L"x3" : L"x2";
      roots.push_back(g_modDir + L"\\Fonts\\" + k);
      Log("crisp fonts: using %ls atlases for scale %.2f", k, s);
    }
    // UI Scale controls in whichever options menu the game loads (base, EUI, ...); also
    // in vanilla mode, where the screenshot driver is started from the options menu
    auto menus = PatchOptionsMenus(gameDir, g_userDir + L"\\UIScale\\cache", g_cfg.patchOptionsMenu,
                               !g_cfg.shots.empty());
    // Vanilla (screenshot "before" runs): only the options menu + shot driver, not the
    // smoothed culture borders
    InstallFileRedirect(gameDir, roots, g_cfg.vanilla ? L"Assets\\UI\\" : L"", menus);
    if (g_cfg.vanilla) Log("Vanilla=1: rendering like the unmodded game (screenshot mode)");
  }
  if (core) InstallD3DHooks();
  if (core && g_cfg.strategicViewFix) InstallStrategicFix(GetCurrentThreadId());  // DllMain runs on the main thread
  else if (core) Log("strategic view anchor fix off (StrategicViewFix=1 enables it; on Windows the "
                     "game's DRM rejects its debug register)");
}

// The same DLL may sit in the game folder under two names (version.dll from an older or a
// Linux install, msimg32.dll on Windows). Only the first copy loaded into the process hooks
// the game; any other one just forwards its exports.
static bool FirstCopyInProcess() {
  wchar_t name[64];
  _snwprintf(name, 64, L"Local\\CivVUIScale.%lu", GetCurrentProcessId());
  name[63] = 0;
  HANDLE m = CreateMutexW(nullptr, FALSE, name);  // kept open for the process lifetime
  return m && GetLastError() != ERROR_ALREADY_EXISTS;
}

static bool g_active = false;

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_DETACH) {
    if (g_active) Log("process detach");
  } else if (reason == DLL_PROCESS_ATTACH) {
    DisableThreadLibraryCalls(inst);
    if (!LoadRealVersionDll()) return FALSE;
    if (!FirstCopyInProcess()) return TRUE;
    g_active = true;
    Init();
  }
  return TRUE;
}
