#pragma once
#include <string>

namespace uis {
void LogInit(const std::wstring& path);
void Log(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
}  // namespace uis
