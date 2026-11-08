#pragma once
#include <string>
#include <utility>
#include <vector>

namespace uis {
// Files under each root mirror paths relative to the game dir: <root>\<rel> replaces
// (or, if absent in the game folder, virtually adds) <game>\<rel>. Later roots win.
// onlyUnder: if set, only overrides below this relative path (e.g. "Assets\\UI\\") are used.
// generated: game-relative path -> replacement file, added after the onlyUnder filter
// (patched options menus, see optionspatch.h).
bool InstallFileRedirect(const std::wstring& gameDir, const std::vector<std::wstring>& roots,
                         const std::wstring& onlyUnder = L"",
                         const std::vector<std::pair<std::wstring, std::wstring>>& generated = {});
}  // namespace uis
