#pragma once
#include <string>
#include <utility>
#include <vector>

namespace uis {
// Adds the UI Scale controls to every options menu the game might load: the base game's
// Assets\UI\Options\OptionsMenu.{lua,xml} and copies shipped by UI mods installed as DLC
// (EUI: Assets\DLC\UI_bc1\Options). Each lua/xml pair is patched from the current game
// files into cacheDir on every launch; nothing in the game folder is modified.
// thirdParty = false: only the base game's menu is patched (PatchOptionsMenu=0).
// Returns game-relative path -> patched copy, for InstallFileRedirect.
std::vector<std::pair<std::wstring, std::wstring>> PatchOptionsMenus(const std::wstring& gameDir,
                                                                     const std::wstring& cacheDir,
                                                                     bool thirdParty, bool shots);

// Text patchers (pure; also used by test/optpatch_test.cpp).
// XML: inserts the (hidden) UIScaleBlock after the PinchSpeedSlider element and names its
// parent if it has no ID. parentId receives the parent's ID.
bool PatchOptionsXml(const std::string& src, std::string& out, std::string& parentId,
                     std::string& why);
// Lua: routes ContextPtr:SetShowHideHandler through a wrapper that also calls
// UIScaleOnShowHide, and appends include("UIScaleOptions"). Line numbers are preserved.
bool PatchOptionsLua(const std::string& src, const std::string& parentId, std::string& out,
                     int& hooks, std::string& why);
}  // namespace uis
