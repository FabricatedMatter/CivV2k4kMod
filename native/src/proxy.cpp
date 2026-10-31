// Forwards every export of the system version.dll so this DLL can sit next to
// the game exe (loaded via WINEDLLOVERRIDES="version=n,b").
#include <windows.h>

#define PROXY_LIST(X)            \
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

#define DECLARE_PTR(name) extern "C" { void* g_real_##name = nullptr; }
PROXY_LIST(DECLARE_PTR)

// Naked jump stubs: signature-agnostic, the stack is untouched.
#define DEFINE_STUB(name)                                                     \
  extern "C" __attribute__((naked)) void proxy_##name() {                     \
    __asm__ volatile("jmp *%0" : : "m"(g_real_##name));                       \
  }
PROXY_LIST(DEFINE_STUB)

namespace uis {
bool LoadRealVersionDll() {
  wchar_t path[MAX_PATH];
  UINT n = GetSystemDirectoryW(path, MAX_PATH);
  if (!n || n > MAX_PATH - 16) return false;
  lstrcatW(path, L"\\version.dll");
  HMODULE real = LoadLibraryW(path);
  if (!real) return false;
#define RESOLVE(name) g_real_##name = (void*)GetProcAddress(real, #name);
  PROXY_LIST(RESOLVE)
  return g_real_GetFileVersionInfoSizeW != nullptr;
}
}  // namespace uis
