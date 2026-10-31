// Logs fatal-looking exceptions (address, registers, exe return addresses on the
// stack) to UIScale.log. Does not handle anything; the game behaves as before.
#include <windows.h>

#include <cstdint>
#include <cstring>

#include "hook.h"
#include "log.h"

namespace uis {
namespace {
volatile LONG g_logged = 0;
uintptr_t g_exeLo = 0, g_exeHi = 0;

void Describe(uintptr_t addr, char* out, size_t n) {
  HMODULE mod = nullptr;
  char name[MAX_PATH] = "?";
  if (GetModuleHandleExA(
          GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
          reinterpret_cast<LPCSTR>(addr), &mod) &&
      mod) {
    char full[MAX_PATH];
    if (GetModuleFileNameA(mod, full, MAX_PATH)) {
      const char* s = strrchr(full, '\\');
      lstrcpynA(name, s ? s + 1 : full, MAX_PATH);
    }
    wsprintfA(out, "%08x (%s+0x%x)", (unsigned)addr, name, (unsigned)(addr - (uintptr_t)mod));
  } else {
    wsprintfA(out, "%08x (?)", (unsigned)addr);
  }
  (void)n;
}

LONG CALLBACK Handler(EXCEPTION_POINTERS* ep) {
  DWORD code = ep->ExceptionRecord->ExceptionCode;
  bool fatal = code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_ILLEGAL_INSTRUCTION ||
               code == EXCEPTION_STACK_OVERFLOW || code == EXCEPTION_INT_DIVIDE_BY_ZERO ||
               code == EXCEPTION_PRIV_INSTRUCTION || code == 0xC0000409 /*stack buffer overrun*/;
  if (!fatal) return EXCEPTION_CONTINUE_SEARCH;
  if (InterlockedIncrement(&g_logged) > 8) return EXCEPTION_CONTINUE_SEARCH;

  CONTEXT* c = ep->ContextRecord;
  char where[300];
  Describe((uintptr_t)ep->ExceptionRecord->ExceptionAddress, where, sizeof(where));
  Log("EXCEPTION %08lx at %s  thread %lu", code, where, GetCurrentThreadId());
  if (code == EXCEPTION_ACCESS_VIOLATION && ep->ExceptionRecord->NumberParameters >= 2)
    Log("  %s address %08lx", ep->ExceptionRecord->ExceptionInformation[0] ? "write" : "read",
        (unsigned long)ep->ExceptionRecord->ExceptionInformation[1]);
  Log("  eax=%08lx ebx=%08lx ecx=%08lx edx=%08lx esi=%08lx edi=%08lx ebp=%08lx esp=%08lx",
      c->Eax, c->Ebx, c->Ecx, c->Edx, c->Esi, c->Edi, c->Ebp, c->Esp);

  // Heuristic stack walk: values that point into the exe's code.
  auto sp = reinterpret_cast<uintptr_t*>(c->Esp);
  int shown = 0;
  for (int i = 0; i < 2048 && shown < 24; ++i) {
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery(sp + i, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT) break;
    uintptr_t v = sp[i];
    if (v >= g_exeLo && v < g_exeHi) {
      Log("  stack[%03x] %08x", i * 4, (unsigned)v);
      ++shown;
    }
  }
  return EXCEPTION_CONTINUE_SEARCH;
}
void LogExeStack(uintptr_t* sp) {
  int shown = 0;
  for (int i = 0; i < 4096 && shown < 16; ++i) {
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery(sp + i, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT) break;
    if (sp[i] >= g_exeLo && sp[i] < g_exeHi) {
      Log("  stack[%03x] %08x", i * 4, (unsigned)sp[i]);
      ++shown;
    }
  }
}

using ExitProcessFn = void(WINAPI*)(UINT);
using TerminateProcessFn = BOOL(WINAPI*)(HANDLE, UINT);
ExitProcessFn g_origExit = nullptr;
TerminateProcessFn g_origTerminate = nullptr;

void WINAPI HookExitProcess(UINT code) {
  char where[300];
  Describe((uintptr_t)__builtin_return_address(0), where, sizeof(where));
  Log("ExitProcess(%u) called from %s", code, where);
  uintptr_t marker;
  LogExeStack(&marker);
  g_origExit(code);
}

BOOL WINAPI HookTerminateProcess(HANDLE h, UINT code) {
  if (h == GetCurrentProcess() || GetProcessId(h) == GetCurrentProcessId()) {
    char where[300];
    Describe((uintptr_t)__builtin_return_address(0), where, sizeof(where));
    Log("TerminateProcess(self, %u) called from %s", code, where);
    uintptr_t marker;
    LogExeStack(&marker);
  }
  return g_origTerminate(h, code);
}
}  // namespace

void InstallCrashLogger() {
  auto base = reinterpret_cast<uint8_t*>(GetModuleHandleW(nullptr));
  auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(
      base + reinterpret_cast<IMAGE_DOS_HEADER*>(base)->e_lfanew);
  auto sec = IMAGE_FIRST_SECTION(nt);
  for (int i = 0; i < nt->FileHeader.NumberOfSections; ++i, ++sec)
    if (sec->Characteristics & IMAGE_SCN_MEM_EXECUTE) {
      g_exeLo = (uintptr_t)base + sec->VirtualAddress;
      g_exeHi = g_exeLo + sec->Misc.VirtualSize;
      break;
    }
  AddVectoredExceptionHandler(1, Handler);
  HMODULE exe = GetModuleHandleW(nullptr);
  g_origExit = reinterpret_cast<ExitProcessFn>(
      PatchIAT(exe, "KERNEL32.dll", "ExitProcess", (void*)&HookExitProcess));
  g_origTerminate = reinterpret_cast<TerminateProcessFn>(
      PatchIAT(exe, "KERNEL32.dll", "TerminateProcess", (void*)&HookTerminateProcess));
}
}  // namespace uis
