#pragma once

namespace uis {

bool InstallCore();  // signature scan + hooks; false => mod stays inert

bool IsActive();
float EffectiveScale();
// Scale the settings ask for (valid before the first resolution apply).
float ConfiguredScale();
float AutoScale();
void GetRealSize(int* w, int* h);
void GetVirtualSize(int* w, int* h);

// Store a new setting (0 = auto) and re-layout the UI like a resolution change.
void SetScaleSetting(float setting, bool apply);
}  // namespace uis
