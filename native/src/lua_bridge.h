#pragma once

namespace uis {
// Hooks luaL_openlibs so every Lua state the game creates gets a `UIScale` table.
bool InstallLuaBridge();
}  // namespace uis
