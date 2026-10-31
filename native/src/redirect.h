#pragma once
#include <string>
#include <vector>

namespace uis {
// Files under each root mirror paths relative to the game dir: <root>\<rel> replaces
// (or, if absent in the game folder, virtually adds) <game>\<rel>. Later roots win.
bool InstallFileRedirect(const std::wstring& gameDir, const std::vector<std::wstring>& roots);
}  // namespace uis
