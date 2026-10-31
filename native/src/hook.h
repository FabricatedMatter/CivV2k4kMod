#pragma once
#include <windows.h>

#include <cstddef>
#include <cstdint>

namespace uis {

// Pattern like "81 EC ?? 03 00 00"; returns 0 if not found or not unique.
uintptr_t SigScan(HMODULE mod, const char* pattern, int* matchCount = nullptr);
// All matches (up to `max`).
int SigScanAll(HMODULE mod, const char* pattern, uintptr_t* out, int max);

// Write bytes into code/data with page protection handled.
bool WriteMem(uintptr_t addr, const void* data, size_t len);

// Replace an import table slot of `mod` (dll name case-insensitive).
// Returns the previous function pointer, or nullptr on failure.
void* PatchIAT(HMODULE mod, const char* dll, const char* func, void* replacement);

// x86 detour: overwrites `stolen` bytes at `target` (must be >= 5 and consist of
// whole, position-independent instructions) with a jmp to `detour`.
// Returns a trampoline that executes the stolen bytes and jumps back.
void* Detour(uintptr_t target, int stolen, void* detour);

template <typename T>
T Read(uintptr_t addr) {
  return *reinterpret_cast<T*>(addr);
}
}  // namespace uis
