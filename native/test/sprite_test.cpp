// Test harness: runs the DLL's tile-sprite shader patcher over files.
// usage: sprite_test.exe IN OUT [IN OUT ...]
#include "../src/d3dhook.cpp"
#include <cstdio>
namespace uis {
bool IsActive() { return true; }
float EffectiveScale() { return 2.0f; }
void Log(const char*, ...) {}
void* PatchIAT(HMODULE, const char*, const char*, void*) { return nullptr; }
float ConfiguredScale() { return 2.0f; }
Config g_cfg;
std::wstring g_modDir;
}
int main(int argc, char** argv) {
  for (int i = 1; i + 1 < argc; i += 2) {
    FILE* f = fopen(argv[i], "rb");
    if (!f) continue;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> in(n); if (fread(in.data(), 1, n, f) != (size_t)n) return 1; fclose(f);
    std::vector<uint8_t> out; int changed = 0;
    if (uis::PatchSpriteShader(in.data(), in.size(), out, &changed)) {
      FILE* o = fopen(argv[i + 1], "wb"); fwrite(out.data(), 1, out.size(), o); fclose(o);
      std::vector<uint8_t> both; int clip = 0;
      if (uis::PatchClipShader(out.data(), out.size(), both, &clip)) {
        FILE* o2 = fopen((std::string(argv[i + 1]) + ".clip").c_str(), "wb");
        fwrite(both.data(), 1, both.size(), o2); fclose(o2);
      }
      printf("%s: %d sprite fetches patched, %d clip\n", argv[i], changed, clip);
    }
  }
}
