// Test harness: runs the DLL's shader patcher over files and writes results.
#include "../src/d3dhook.cpp"
#include <cstdio>
namespace uis {
bool IsActive() { return true; }
float EffectiveScale() { return 2.0f; }
void Log(const char*, ...) {}
void* PatchIAT(HMODULE, const char*, const char*, void*) { return nullptr; }
float ConfiguredScale() { return 2.0f; }
void GetVirtualSize(int* w, int* h) { *w = 1920; *h = 1080; }
Config g_cfg;
std::wstring g_modDir;
}
int main(int argc, char** argv) {
  int patched = 0;
  for (int i = 1; i + 1 < argc; i += 2) {
    FILE* f = fopen(argv[i], "rb");
    if (!f) continue;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> in(n); if (fread(in.data(), 1, n, f) != (size_t)n) return 1; fclose(f);
    std::vector<uint8_t> out; int changed = 0;
    if (uis::PatchClipShader(in.data(), in.size(), out, &changed)) {
      FILE* o = fopen(argv[i + 1], "wb"); fwrite(out.data(), 1, out.size(), o); fclose(o);
      ++patched;
    }
  }
  printf("patched %d\n", patched);
}
