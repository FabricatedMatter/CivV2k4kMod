#pragma once

namespace uis {

bool InstallCore();  // signature scan + hooks; false => mod stays inert

bool IsActive();
float EffectiveScale();
// Non-null if the mod had to fall back to safe mode (shown in the options menu).
const char* Warning();
// Scale the settings ask for (valid before the first resolution apply).
float ConfiguredScale();
float AutoScale();
void GetRealSize(int* w, int* h);
void GetVirtualSize(int* w, int* h);

// Store a new setting (0 = auto) and re-layout the UI like a resolution change.
void SetScaleSetting(float setting, bool apply);

// Screenshot automation: ignore the real mouse from now on and park the game's cursor
// (ShotMouse=<fx>,<fy> of the client area, default 0.98,0.97). Only with Shots=<run>.
void LockMouseForShots();
void UnlockMouseForShots();
}  // namespace uis
