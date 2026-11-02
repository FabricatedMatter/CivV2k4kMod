#pragma once
#include <string>

namespace uis {

struct Config {
  // 0 = automatic (real height / 1080), otherwise an explicit factor.
  float scale = 0.0f;
  float minScale = 0.5f;
  float maxScale = 4.0f;
  bool crispFonts = true;
  bool aiArt = true;  // AI-upscaled UI textures (tools/uiart.py)
  // Align world anchors in strategic view (costs some FPS in that view only).
  bool strategicViewFix = true;
  bool logMouse = false;
  bool dumpTextures = false;  // UI art diagnostics (1: also dump uploads to UIScale/dump)
  bool dumpPixels = false;
  bool failWarning = true;  // message box when falling back to safe mode
  bool testSafeMode = false;  // simulate a hook failure (to test the safe mode)
  // Experimental: render the map overlay pages (borders, fog) at N x resolution.
  int overlayScale = 0;
  bool overlayDiag = false;  // DumpTextures=3: log overlay page usage
};

extern Config g_cfg;
extern std::wstring g_userDir;  // ...\My Games\Sid Meier's Civilization 5
extern std::wstring g_modDir;   // <game dir>\UIScale

void LoadConfig();
void SaveConfig();
}  // namespace uis
