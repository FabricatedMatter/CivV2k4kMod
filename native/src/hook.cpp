#include "hook.h"

#include <cctype>
#include <cstring>
#include <vector>

#include "log.h"

namespace uis {

static bool ParsePattern(const char* p, std::vector<int>& out) {
  while (*p) {
    while (*p == ' ') ++p;
    if (!*p) break;
    if (p[0] == '?') {
      out.push_back(-1);
      p += (p[1] == '?') ? 2 : 1;
      continue;
    }
    if (!isxdigit((unsigned char)p[0]) || !isxdigit((unsigned char)p[1])) return false;
    char b[3] = {p[0], p[1], 0};
    out.push_back((int)strtoul(b, nullptr, 16));
    p += 2;
  }
  return !out.empty();
}

int SigScanAll(HMODULE mod, const char* pattern, uintptr_t* out, int max) {
  std::vector<int> pat;
  if (!ParsePattern(pattern, pat)) return 0;
  auto base = reinterpret_cast<uint8_t*>(mod);
  auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
  auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
  auto sec = IMAGE_FIRST_SECTION(nt);
  int count = 0;
  for (int s = 0; s < nt->FileHeader.NumberOfSections; ++s, ++sec) {
    if (!(sec->Characteristics & IMAGE_SCN_MEM_EXECUTE)) continue;
    uint8_t* start = base + sec->VirtualAddress;
    size_t size = sec->Misc.VirtualSize;
    if (size < pat.size()) continue;
    for (size_t i = 0; i + pat.size() <= size; ++i) {
      size_t j = 0;
      for (; j < pat.size(); ++j)
        if (pat[j] >= 0 && start[i + j] != (uint8_t)pat[j]) break;
      if (j == pat.size()) {
        if (count < max) out[count] = (uintptr_t)(start + i);
        ++count;
      }
    }
  }
  return count;
}

uintptr_t SigScan(HMODULE mod, const char* pattern, int* matchCount) {
  uintptr_t first = 0;
  int n = SigScanAll(mod, pattern, &first, 1);
  if (matchCount) *matchCount = n;
  return n == 1 ? first : 0;
}

bool WriteMem(uintptr_t addr, const void* data, size_t len) {
  DWORD old;
  if (!VirtualProtect(reinterpret_cast<void*>(addr), len, PAGE_EXECUTE_READWRITE, &old))
    return false;
  memcpy(reinterpret_cast<void*>(addr), data, len);
  VirtualProtect(reinterpret_cast<void*>(addr), len, old, &old);
  FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(addr), len);
  return true;
}

void* PatchIAT(HMODULE mod, const char* dll, const char* func, void* replacement) {
  auto base = reinterpret_cast<uint8_t*>(mod);
  auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
  auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
  auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
  if (!dir.VirtualAddress) return nullptr;
  for (auto imp = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress); imp->Name;
       ++imp) {
    if (_stricmp(reinterpret_cast<char*>(base + imp->Name), dll) != 0) continue;
    if (!imp->OriginalFirstThunk) continue;
    auto names = reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->OriginalFirstThunk);
    auto slots = reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->FirstThunk);
    for (; names->u1.AddressOfData; ++names, ++slots) {
      if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
      auto byName = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base + names->u1.AddressOfData);
      if (strcmp(reinterpret_cast<char*>(byName->Name), func) != 0) continue;
      DWORD old;
      void** slot = reinterpret_cast<void**>(&slots->u1.Function);
      if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) return nullptr;
      void* prev = *slot;
      *slot = replacement;
      VirtualProtect(slot, sizeof(void*), old, &old);
      return prev;
    }
  }
  return nullptr;
}

void* Detour(uintptr_t target, int stolen, void* detour) {
  if (stolen < 5) return nullptr;
  auto tramp = static_cast<uint8_t*>(
      VirtualAlloc(nullptr, 64, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
  if (!tramp) return nullptr;
  memcpy(tramp, reinterpret_cast<void*>(target), stolen);
  tramp[stolen] = 0xE9;
  *reinterpret_cast<int32_t*>(tramp + stolen + 1) =
      (int32_t)(target + stolen - (uintptr_t)(tramp + stolen + 5));

  DWORD old;
  if (!VirtualProtect(reinterpret_cast<void*>(target), stolen, PAGE_EXECUTE_READWRITE, &old))
    return nullptr;
  auto p = reinterpret_cast<uint8_t*>(target);
  p[0] = 0xE9;
  *reinterpret_cast<int32_t*>(p + 1) = (int32_t)((uintptr_t)detour - (target + 5));
  for (int i = 5; i < stolen; ++i) p[i] = 0x90;
  VirtualProtect(reinterpret_cast<void*>(target), stolen, old, &old);
  FlushInstructionCache(GetCurrentProcess(), reinterpret_cast<void*>(target), stolen);
  return tramp;
}
}  // namespace uis
