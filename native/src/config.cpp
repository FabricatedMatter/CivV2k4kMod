#include "config.h"

#include <windows.h>

#include <cstdio>
#include <cwchar>

namespace uis {

Config g_cfg;
std::wstring g_userDir;
std::wstring g_modDir;

static std::wstring IniPath() { return g_userDir + L"\\UIScale.ini"; }

static float ReadFloat(const wchar_t* key, float def) {
  wchar_t buf[64];
  wchar_t defs[64];
  swprintf(defs, 64, L"%g", def);
  GetPrivateProfileStringW(L"UIScale", key, defs, buf, 64, IniPath().c_str());
  return (float)_wtof(buf);
}

void LoadConfig() {
  g_cfg.scale = ReadFloat(L"Scale", 0.0f);
  g_cfg.minScale = ReadFloat(L"MinScale", 0.5f);
  g_cfg.maxScale = ReadFloat(L"MaxScale", 4.0f);
  g_cfg.crispFonts = ReadFloat(L"CrispFonts", 1.0f) != 0.0f;
  g_cfg.logMouse = ReadFloat(L"LogMouse", 0.0f) != 0.0f;
  g_cfg.strategicViewFix = ReadFloat(L"StrategicViewFix", 1.0f) != 0.0f;
}

void SaveConfig() {
  wchar_t buf[64];
  swprintf(buf, 64, L"%.2f", g_cfg.scale);
  WritePrivateProfileStringW(L"UIScale", L"Scale", buf, IniPath().c_str());
  WritePrivateProfileStringW(L"UIScale", L"CrispFonts", g_cfg.crispFonts ? L"1" : L"0",
                             IniPath().c_str());
}
}  // namespace uis
