// Strategic-view world anchors (yield icons, city banners in the 2D map view).
//
// In strategic view the engine positions world anchors with a 2D camera
// transform that yields *real* screen pixels:  (p - center) * zoom + screen/2.
// The UI works in virtual units, so the result has to be divided by the scale.
// The call site is a direct call inside the CEG-protected code, so instead of
// patching it we place a hardware execution breakpoint (debug register DR0) on
// the instruction right after the call and fix the two output floats in a
// vectored exception handler. No code bytes are modified.
#include <windows.h>

#include "core.h"
#include "hook.h"
#include "log.h"

namespace uis {
namespace {
uintptr_t g_bp = 0;       // instruction after the strategic projection call
DWORD g_mainThread = 0;
volatile LONG g_hits = 0;

LONG CALLBACK Handler(EXCEPTION_POINTERS* ep) {
  if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_SINGLE_STEP ||
      (uintptr_t)ep->ExceptionRecord->ExceptionAddress != g_bp)
    return EXCEPTION_CONTINUE_SEARCH;
  CONTEXT* c = ep->ContextRecord;
  float s = EffectiveScale();
  if (s != 1.0f) {
    auto out = reinterpret_cast<float*>(c->Esp + 0x40);  // screen x, y written by the call
    out[0] /= s;
    out[1] /= s;
  }
  c->EFlags |= 0x10000;  // RF: execute the instruction without re-triggering
  LONG n = InterlockedIncrement(&g_hits);
  if (n == 1) Log("strategic view: anchor fix active");
  return EXCEPTION_CONTINUE_EXECUTION;
}

DWORD WINAPI SetBreakpointThread(LPVOID) {
  HANDLE th = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_SUSPEND_RESUME, FALSE,
                         g_mainThread);
  if (!th) {
    Log("strategic fix: OpenThread failed (%lu)", GetLastError());
    return 0;
  }
  SuspendThread(th);
  CONTEXT c = {};
  c.ContextFlags = CONTEXT_DEBUG_REGISTERS;
  BOOL ok = GetThreadContext(th, &c);
  if (ok) {
    c.Dr0 = g_bp;
    c.Dr7 = (c.Dr7 & ~0x000F0003ul) | 0x1;  // L0, execute, 1 byte
    ok = SetThreadContext(th, &c);
  }
  CONTEXT v = {};
  v.ContextFlags = CONTEXT_DEBUG_REGISTERS;
  GetThreadContext(th, &v);
  ResumeThread(th);
  CloseHandle(th);
  Log("strategic fix: breakpoint %s (DR0=%08lx DR7=%08lx)",
      ok && v.Dr0 == g_bp ? "set" : "FAILED", (unsigned long)v.Dr0, (unsigned long)v.Dr7);
  return 0;
}
}  // namespace

void InstallStrategicFix(DWORD mainThreadId) {
  HMODULE exe = GetModuleHandleW(nullptr);
  int n = 0;
  uintptr_t m = SigScan(exe,
                        "B9 ?? ?? ?? ?? F3 0F 11 44 24 68 E8 ?? ?? ?? ?? F3 0F 10 44 24 40 "
                        "F3 0F 10 4C 24 44",
                        &n);
  if (!m) {
    Log("strategic fix: signature not found (%d matches)", n);
    return;
  }
  g_bp = m + 16;
  g_mainThread = mainThreadId;
  AddVectoredExceptionHandler(1, Handler);
  // Debug registers of the main thread must be set from another thread.
  HANDLE t = CreateThread(nullptr, 0, SetBreakpointThread, nullptr, 0, nullptr);
  if (t) CloseHandle(t);
}
}  // namespace uis
