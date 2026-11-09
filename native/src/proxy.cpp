// Forwards every export of the system version.dll and msimg32.dll, so this one DLL can sit
// next to the game exe under either name:
//   Linux / Proton: version.dll, loaded via WINEDLLOVERRIDES="version=n,b"
//   Windows:        msimg32.dll. Windows applies compatibility shims to Civ V (apphelp +
//                   AcGenral.dll); AcGenral imports the system version.dll before the exe's
//                   own imports are resolved, so a version.dll next to the exe is never
//                   loaded there. msimg32.dll is imported by the exe, is not a KnownDLL and
//                   is not pulled in by the shims.
#include <windows.h>

#define VERSION_LIST(X)          \
  X(GetFileVersionInfoA)         \
  X(GetFileVersionInfoByHandle)  \
  X(GetFileVersionInfoExA)       \
  X(GetFileVersionInfoExW)       \
  X(GetFileVersionInfoSizeA)     \
  X(GetFileVersionInfoSizeExA)   \
  X(GetFileVersionInfoSizeExW)   \
  X(GetFileVersionInfoSizeW)     \
  X(GetFileVersionInfoW)         \
  X(VerFindFileA)                \
  X(VerFindFileW)                \
  X(VerInstallFileA)             \
  X(VerInstallFileW)             \
  X(VerLanguageNameA)            \
  X(VerLanguageNameW)            \
  X(VerQueryValueA)              \
  X(VerQueryValueW)

#define MSIMG32_LIST(X) \
  X(AlphaBlend)         \
  X(DllInitialize)      \
  X(GradientFill)       \
  X(TransparentBlt)     \
  X(vSetDdrawflag)

#define DECLARE_PTR(name) extern "C" { void* g_real_##name = nullptr; }
VERSION_LIST(DECLARE_PTR)
MSIMG32_LIST(DECLARE_PTR)

// Naked jump stubs: signature-agnostic, the stack is untouched.
#define DEFINE_STUB(name)                                                     \
  extern "C" __attribute__((naked)) void proxy_##name() {                     \
    __asm__ volatile("jmp *%0" : : "m"(g_real_##name));                       \
  }
VERSION_LIST(DEFINE_STUB)
MSIMG32_LIST(DEFINE_STUB)

namespace uis {
static HMODULE LoadSystemDll(const wchar_t* name) {
  wchar_t path[MAX_PATH];
  UINT n = GetSystemDirectoryW(path, MAX_PATH);
  if (!n || n > MAX_PATH - 16) return nullptr;
  lstrcatW(path, L"\\");
  lstrcatW(path, name);
  return LoadLibraryW(path);
}

bool LoadRealVersionDll() {
  HMODULE ver = LoadSystemDll(L"version.dll");
  HMODULE img = LoadSystemDll(L"msimg32.dll");
  if (!ver || !img) return false;
#define RESOLVE_VER(name) g_real_##name = (void*)GetProcAddress(ver, #name);
#define RESOLVE_IMG(name) g_real_##name = (void*)GetProcAddress(img, #name);
  VERSION_LIST(RESOLVE_VER)
  MSIMG32_LIST(RESOLVE_IMG)
  return g_real_GetFileVersionInfoSizeW != nullptr && g_real_AlphaBlend != nullptr;
}
}  // namespace uis
