#include "config.h"

#include <windows.h>

#include <cstdio>
#include <cwchar>

namespace uis {

Config g_cfg;
std::wstring g_userDir;
std::wstring g_modDir;

static std::wstring IniPath() { return g_userDir + L"\\UIScale.ini"; }

bool UnderWine() {
  HMODULE nt = GetModuleHandleW(L"ntdll.dll");
  return nt && GetProcAddress(nt, "wine_get_version");
}

static float ReadFloat(const wchar_t* key, float def) {
  wchar_t buf[64];
  wchar_t defs[64];
  swprintf(defs, 64, L"%g", def);
  GetPrivateProfileStringW(L"UIScale", key, defs, buf, 64, IniPath().c_str());
  return (float)_wtof(buf);
}

static std::wstring ReadString(const wchar_t* key) {
  wchar_t buf[512];
  GetPrivateProfileStringW(L"UIScale", key, L"", buf, 512, IniPath().c_str());
  return buf;
}

void LoadConfig() {
  g_cfg.scale = ReadFloat(L"Scale", 0.0f);
  g_cfg.minScale = ReadFloat(L"MinScale", 0.5f);
  g_cfg.maxScale = ReadFloat(L"MaxScale", 4.0f);
  g_cfg.crispFonts = ReadFloat(L"CrispFonts", 1.0f) != 0.0f;
  g_cfg.logMouse = ReadFloat(L"LogMouse", 0.0f) != 0.0f;
  g_cfg.aiArt = ReadFloat(L"AIArt", 1.0f) != 0.0f;
  float dump = ReadFloat(L"DumpTextures", 0.0f);
  g_cfg.dumpTextures = dump != 0.0f;
  g_cfg.dumpPixels = dump == 1.0f;
  g_cfg.overlayDiag = dump == 3.0f;
  g_cfg.patchOptionsMenu = ReadFloat(L"PatchOptionsMenu", 1.0f) != 0.0f;
  g_cfg.failWarning = ReadFloat(L"FailWarning", 1.0f) != 0.0f;
  g_cfg.testSafeMode = ReadFloat(L"TestSafeMode", 0.0f) != 0.0f;
  g_cfg.overlayScale = (int)ReadFloat(L"OverlayScale", 0.0f);
  if (g_cfg.overlayScale < 2 || g_cfg.overlayScale > 4) g_cfg.overlayScale = 0;
  // The fix uses a hardware breakpoint (debug register). On Windows the game's Steam DRM
  // (CEG) detects debug registers and crashes the game on purpose about 15 s after start,
  // so it is only on by default under Wine / Proton, where that check does not trigger.
  g_cfg.strategicViewFix = ReadFloat(L"StrategicViewFix", UnderWine() ? 1.0f : 0.0f) != 0.0f;
  g_cfg.shots = ReadString(L"Shots");
  g_cfg.artTrace = ReadString(L"ArtTrace");
  g_cfg.sampleBias = ReadFloat(L"SampleBias", 1.0f) != 0.0f;
  g_cfg.smoothScaling = ReadFloat(L"SmoothScaling", 1.0f) != 0.0f;
  g_cfg.backgroundCover = ReadFloat(L"BackgroundCover", 1.0f) != 0.0f;
  g_cfg.shotSave = ReadString(L"ShotSave");
  g_cfg.shotScales = ReadString(L"ShotScales");
  g_cfg.shotLook = ReadString(L"ShotLook");
  g_cfg.shotMouse = ReadString(L"ShotMouse");
  g_cfg.vanilla = ReadFloat(L"Vanilla", 0.0f) != 0.0f;
  g_cfg.traceFiles = ReadFloat(L"TraceFiles", 0.0f) != 0.0f;
  if (g_cfg.vanilla) {
    g_cfg.scale = 1.0f;
    g_cfg.crispFonts = false;
    g_cfg.aiArt = false;
    g_cfg.strategicViewFix = false;
    g_cfg.overlayScale = 0;
  }
}

bool ShotsStillRequested() {
  return !ReadString(L"Shots").empty();
}

void SaveConfig() {
  if (g_cfg.vanilla) return;  // the forced settings are not the player's
  wchar_t buf[64];
  swprintf(buf, 64, L"%.2f", g_cfg.scale);
  WritePrivateProfileStringW(L"UIScale", L"Scale", buf, IniPath().c_str());
  WritePrivateProfileStringW(L"UIScale", L"CrispFonts", g_cfg.crispFonts ? L"1" : L"0",
                             IniPath().c_str());
}
}  // namespace uis
