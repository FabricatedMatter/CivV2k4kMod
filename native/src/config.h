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
  // Add the UI Scale controls to options menus of UI mods (EUI) too; 0 = base game only.
  bool patchOptionsMenu = true;
  bool failWarning = true;  // message box when falling back to safe mode
  bool testSafeMode = false;  // simulate a hook failure (to test the safe mode)
  // Experimental: render the map overlay pages (borders, fog) at N x resolution.
  int overlayScale = 0;
  bool overlayDiag = false;  // DumpTextures=3: log overlay page usage
  // ArtTrace=a,b (with DumpTextures): log every draw that uses a replaced texture whose
  // name contains one of these strings, with its pixel shader (no 300-draw cap)
  std::wstring artTrace;
  bool sampleBias = true;  // SampleBias=0: no 1/32 px sample-point bias in UI shaders
  // Screenshot automation (tools/shots.py). Vanilla=1: the mod stays loaded but renders
  // like the unmodded game (scale 1, original art, fonts and borders), so the same driver
  // can take the "before" shots.
  bool vanilla = false;
  std::wstring shots;       // Shots=<run name>: run tools/shots.py's driver (empty = off)
  std::wstring shotSave;    // ShotSave=<save file name or path>
  std::wstring shotScales;  // ShotScales=0,1.5,2 (0 = auto)
  std::wstring shotMouse;   // ShotMouse=<fx>,<fy>: parked cursor (fraction of the window)
  std::wstring shotLook;    // ShotLook=<city name> or <x>,<y>: camera target (empty = as loaded)
};

extern Config g_cfg;
extern std::wstring g_userDir;  // ...\My Games\Sid Meier's Civilization 5
extern std::wstring g_modDir;   // <game dir>\UIScale

void LoadConfig();
void SaveConfig();
// Shots= is still set in UIScale.ini (tools/shots.py removes it when it stops)
bool ShotsStillRequested();
}  // namespace uis
