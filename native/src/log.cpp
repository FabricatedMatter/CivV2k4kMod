#include "log.h"

#include <windows.h>

#include <cstdarg>
#include <cstdio>

namespace uis {
static FILE* g_log = nullptr;
static CRITICAL_SECTION g_cs;

void LogInit(const std::wstring& path) {
  InitializeCriticalSection(&g_cs);
  g_log = _wfopen(path.c_str(), L"w");
}

void Log(const char* fmt, ...) {
  if (!g_log) return;
  EnterCriticalSection(&g_cs);
  SYSTEMTIME t;
  GetLocalTime(&t);
  fprintf(g_log, "[%02d:%02d:%02d.%03d] ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
  va_list ap;
  va_start(ap, fmt);
  vfprintf(g_log, fmt, ap);
  va_end(ap);
  fputc('\n', g_log);
  fflush(g_log);
  LeaveCriticalSection(&g_cs);
}
}  // namespace uis
