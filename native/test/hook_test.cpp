// Test harness for the context hooks on the real d3d11 runtime (Windows, 32-bit build):
//   g++ -std=c++17 -static -DUNICODE -D_UNICODE -DWIN32_LEAN_AND_MEAN -o build/hook_test.exe \
//       test/hook_test.cpp -ld3d11
//   build/hook_test.exe CLIP_SHADER.dxbc      (one of the game's UI clip pixel shaders)
// Checks that the clip scale ends up in slot 13 of the game device's immediate context and
// stays there when Microsoft's d3d11 rewrites the context's method table (draw entries
// after Flush, every entry when multithread protection changes).
#include "../src/d3dhook.cpp"
#include <cstdarg>
#include <cstdio>
namespace uis {
bool IsActive() { return true; }
float EffectiveScale() { return 2.0f; }
void Log(const char* f, ...) {
  va_list a;
  va_start(a, f);
  printf("  log: ");
  vprintf(f, a);
  printf("\n");
  va_end(a);
}
void* PatchIAT(HMODULE, const char*, const char*, void*) { return nullptr; }
float ConfiguredScale() { return 2.0f; }
void GetVirtualSize(int* w, int* h) { *w = 1920; *h = 1080; }
Config g_cfg;
std::wstring g_modDir;
}  // namespace uis

using namespace uis;
static int g_fail = 0;
static void Check(bool ok, const char* what) {
  printf("%s %s\n", ok ? "ok  " : "FAIL", what);
  g_fail += !ok;
}
static bool ScaleBound(ID3D11DeviceContext* c) {
  ID3D11Buffer* b = nullptr;
  c->PSGetConstantBuffers(kScaleSlot, 1, &b);
  bool ok = b && b == CurrentScaleBuffer(c);
  if (b) b->Release();
  return ok;
}
static bool DrawHooked(ID3D11DeviceContext* c, int slot) {
  void** vt = *reinterpret_cast<void***>(c);
  return vt[kVtDraw] == PICK_SLOT(HookDraw) && vt[kVtDrawIndexed] == PICK_SLOT(HookDrawIndexed);
}

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  FILE* f = fopen(argv[1], "rb");
  if (!f) return 2;
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  std::vector<char> bc(n);
  if (fread(bc.data(), 1, n, f) != (size_t)n) return 2;
  fclose(f);

  InitializeCriticalSection(&g_cs);
  InitializeCriticalSection(&g_hookCs);
  HookD3DVtables(1);  // helper device, as at CreateWindowExA
  Check(g_vtHooked, "hooks installed from a helper device");

  ID3D11Device* dev = nullptr;
  ID3D11DeviceContext* imm = nullptr;
  if (FAILED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                               D3D11_SDK_VERSION, &dev, nullptr, &imm)))
    return 2;
  ID3D11PixelShader* ps = nullptr;
  Check(SUCCEEDED(dev->CreatePixelShader(bc.data(), n, nullptr, &ps)) && ps,
        "game device: CreatePixelShader");
  Check(g_patched.count(ps) == 1, "the clip shader was patched");
  int slot = 0;
  while (slot < kCtxSlots && g_ctxVt[slot] != *reinterpret_cast<void***>(imm)) ++slot;
  Check(slot < kCtxSlots, "game immediate context table hooked");

  imm->PSSetShader(ps, nullptr, 0);
  Check(ScaleBound(imm), "PSSetShader binds the clip scale");

  ID3D11Buffer* none = nullptr;
  imm->PSSetConstantBuffers(kScaleSlot, 1, &none);
  Check(ScaleBound(imm), "game clearing slot 13 while the shader is set: rebound");

  imm->Flush();
  printf("     after Flush: draw entries still hooked = %d\n", (int)DrawHooked(imm, slot));
  imm->PSSetShader(ps, nullptr, 0);
  Check(DrawHooked(imm, slot), "draw hooks restored at the next PSSetShader");

  ID3D10Multithread* mt = nullptr;
  if (SUCCEEDED(imm->QueryInterface(__uuidof(ID3D10Multithread), (void**)&mt)) && mt) {
    mt->SetMultithreadProtected(TRUE);
    void** vt = *reinterpret_cast<void***>(imm);
    printf("     after SetMultithreadProtected: PSSetShader hooked = %d\n",
           (int)(vt[kVtPSSetShader] == PICK_SLOT(HookPSSetShader)));
    VerifyD3DHooks();  // once per frame in the game
    Check(vt[kVtPSSetShader] == PICK_SLOT(HookPSSetShader), "per-frame check restores PSSetShader");
    imm->PSSetConstantBuffers(kScaleSlot, 1, &none);
    imm->PSSetShader(nullptr, nullptr, 0);
    imm->PSSetShader(ps, nullptr, 0);
    Check(ScaleBound(imm), "clip scale bound after the runtime rewrote the table");
    mt->SetMultithreadProtected(FALSE);
    mt->Release();
  }

  // the game creates its own deferred contexts (8 on the test PC) and draws the UI with them;
  // with Microsoft's d3d11 each has its own table
  {
    const int kDef = 12;
    ID3D11DeviceContext* defs[kDef] = {};
    int bound = 0;
    for (int i = 0; i < kDef; ++i) {
      if (FAILED(dev->CreateDeferredContext(0, &defs[i])) || !defs[i]) continue;
      defs[i]->PSSetShader(ps, nullptr, 0);
      bound += ScaleBound(defs[i]);
    }
    printf("     %d of %d game deferred contexts bind the clip scale\n", bound, kDef);
    Check(bound == kDef, "every deferred context the game creates binds the clip scale");
    for (auto* d : defs)
      if (d) d->Release();
  }
  int before = g_hookLogs;
  for (int i = 0; i < 1000; ++i) VerifyD3DHooks();
  Check(g_hookLogs == before, "per-frame checks with nothing to do log nothing");

  // a second device: its contexts must get a buffer of their own device
  ID3D11Device* dev2 = nullptr;
  ID3D11DeviceContext* imm2 = nullptr;
  if (SUCCEEDED(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                  D3D11_SDK_VERSION, &dev2, nullptr, &imm2))) {
    ID3D11PixelShader* ps2 = nullptr;
    dev2->CreatePixelShader(bc.data(), n, nullptr, &ps2);
    imm2->PSSetShader(ps2, nullptr, 0);
    ID3D11Buffer* b = nullptr;
    imm2->PSGetConstantBuffers(kScaleSlot, 1, &b);
    ID3D11Device* bd = nullptr;
    if (b) b->GetDevice(&bd);
    Check(b && bd == dev2, "second device: clip scale buffer of its own device bound");
    if (bd) bd->Release();
    if (b) b->Release();
  }
  printf("%s\n", g_fail ? "FAILED" : "all passed");
  return g_fail != 0;
}
