// D3D11-level fix for clipped UI content.
//
// Civ V's UI pixel shaders clip against a rect in a constant buffer:
//     add r0.xy, v_pos.xyxx, -cbN[k].xzxx ; lt r0.xy, r0.xyxx, 0 ; discard_nz ...
// SV_Position is in real pixels while the engine supplies the rect in UI units,
// so at scale != 1 everything inside scroll panels / edit boxes is discarded.
// When such a shader is created we rewrite each clip `add` into
//     mad r0.xy, (+-)cbN[k], cb13[0].xxxx, (+-)v_pos      (= pos - clip * scale)
// and bind a tiny constant buffer holding the current scale to slot 13 whenever
// a patched shader is set. Method tables live in d3d11.dll (DXVK), not in the
// CEG-protected exe. Prototype + validation: tools/dxbc_clipfix.py.
#include <d3d11.h>
#include <windows.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "config.h"
#include "core.h"
#include "hook.h"
#include "log.h"

namespace uis {
namespace {

// ---------------------------------------------------------------- DXBC patch
constexpr uint32_t kOpAdd = 0, kOpLt = 49, kOpMad = 50, kOpCustomData = 53;
constexpr uint32_t kOpDclInputPsSiv = 100;
constexpr uint32_t kScaleSlot = 13;
const uint32_t kScaleOperand[3] = {0x00208006, kScaleSlot, 0};          // cb13[0].xxxx
const uint32_t kScaleDcl[4] = {0x04000059, 0x00208E46, kScaleSlot, 1};  // dcl_constantbuffer cb13[1]

uint32_t InstrLen(const uint32_t* t) {
  return (t[0] & 0x7FF) == kOpCustomData ? t[1] : (t[0] >> 24) & 0x7F;
}

// Length of the operand at t; type and first index returned via out params.
bool ParseOperand(const uint32_t* t, const uint32_t* end, uint32_t* len, uint32_t* type,
                  uint32_t* idx0) {
  uint32_t tok = t[0], n = 1;
  if (tok & 0x80000000) ++n;
  *type = (tok >> 12) & 0xFF;
  *idx0 = 0xFFFFFFFF;
  if (*type == 4) {  // immediate32
    uint32_t nc = tok & 3;
    n += nc == 1 ? 1 : (nc == 2 ? 4 : 0);
  } else {
    uint32_t dim = (tok >> 20) & 3;
    for (uint32_t d = 0; d < dim; ++d) {
      if (((tok >> (22 + 3 * d)) & 7) != 0) return false;  // relative addressing
      if (d == 0 && t + n < end) *idx0 = t[n];
      ++n;
    }
  }
  if (t + n > end) return false;
  *len = n;
  return true;
}

void Md5Transform(uint32_t st[4], const uint8_t* blk) {
  static const uint32_t K[64] = {
      0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613,
      0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193,
      0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d,
      0x02441453, 0xd8a1e681, 0xe7d3fbc8, 0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
      0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122,
      0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
      0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665, 0xf4292244,
      0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
      0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb,
      0xeb86d391};
  static const uint8_t R[64] = {7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
                                5, 9,  14, 20, 5, 9,  14, 20, 5, 9,  14, 20, 5, 9,  14, 20,
                                4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
                                6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21};
  uint32_t M[16];
  memcpy(M, blk, 64);
  uint32_t a = st[0], b = st[1], c = st[2], d = st[3];
  for (int i = 0; i < 64; ++i) {
    uint32_t f, g;
    if (i < 16) f = (b & c) | (~b & d), g = i;
    else if (i < 32) f = (d & b) | (~d & c), g = (5 * i + 1) % 16;
    else if (i < 48) f = b ^ c ^ d, g = (3 * i + 5) % 16;
    else f = c ^ (b | ~d), g = (7 * i) % 16;
    f += a + K[i] + M[g];
    a = d;
    d = c;
    c = b;
    b += (f << R[i]) | (f >> (32 - R[i]));
  }
  st[0] += a, st[1] += b, st[2] += c, st[3] += d;
}

// DXBC container checksum (MD5 variant over everything after the hash field).
void DxbcChecksum(std::vector<uint8_t>& blob) {
  const uint8_t* data = blob.data() + 20;
  uint32_t n = (uint32_t)blob.size() - 20, bits = n * 8;
  uint32_t st[4] = {0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476};
  uint32_t full = n / 64, rest = n % 64;
  for (uint32_t k = 0; k < full; ++k) Md5Transform(st, data + 64 * k);
  uint8_t blk[64] = {};
  uint32_t tail = (bits >> 2) | 1;
  if (rest >= 56) {
    memcpy(blk, data + 64 * full, rest);
    blk[rest] = 0x80;
    Md5Transform(st, blk);
    memset(blk, 0, 64);
    memcpy(blk, &bits, 4);
    memcpy(blk + 60, &tail, 4);
  } else {
    memcpy(blk, &bits, 4);
    memcpy(blk + 4, data + 64 * full, rest);
    blk[4 + rest] = 0x80;
    memcpy(blk + 60, &tail, 4);
  }
  Md5Transform(st, blk);
  memcpy(blob.data() + 4, st, 16);
}

bool PatchClipShader(const void* bc, SIZE_T len, std::vector<uint8_t>& out, int* changed) {
  auto b = static_cast<const uint8_t*>(bc);
  if (len < 32 || memcmp(b, "DXBC", 4) != 0) return false;
  uint32_t nch, total;
  memcpy(&total, b + 24, 4);
  memcpy(&nch, b + 28, 4);
  if (total != len || 32 + 4 * nch > len) return false;
  uint32_t so = 0, ssize = 0;
  for (uint32_t c = 0; c < nch; ++c) {
    uint32_t o;
    memcpy(&o, b + 32 + 4 * c, 4);
    if (o + 8 <= len && (!memcmp(b + o, "SHEX", 4) || !memcmp(b + o, "SHDR", 4))) {
      so = o;
      memcpy(&ssize, b + o + 4, 4);
    }
  }
  if (!so || so + 8 + ssize > len || ssize < 8) return false;
  const uint32_t* toks = reinterpret_cast<const uint32_t*>(b + so + 8);
  const uint32_t* end = toks + ssize / 4;
  if ((toks[0] >> 16) != 0) return false;  // not a pixel shader

  // Instruction boundaries + SV_Position input register.
  std::vector<const uint32_t*> ins;
  uint32_t posReg = 0xFFFFFFFF;
  for (const uint32_t* p = toks + 2; p < end;) {
    uint32_t l = InstrLen(p);
    if (!l || p + l > end) return false;
    if ((p[0] & 0x7FF) == kOpDclInputPsSiv && l >= 4 && p[l - 1] == 1 /*position*/) {
      uint32_t ol, ty, idx;
      if (ParseOperand(p + 1, p + l, &ol, &ty, &idx)) posReg = idx;
    }
    ins.push_back(p);
    p += l;
  }
  if (posReg == 0xFFFFFFFF) return false;

  std::vector<uint32_t> o(toks, toks + 2);
  bool dclDone = false;
  *changed = 0;
  for (size_t k = 0; k < ins.size(); ++k) {
    const uint32_t* p = ins[k];
    uint32_t op = p[0] & 0x7FF, l = InstrLen(p);
    if (!dclDone && !(op >= 88 && op <= 106)) {
      o.insert(o.end(), kScaleDcl, kScaleDcl + 4);
      dclDone = true;
    }
    if (op == kOpAdd && !(p[0] & 0x80000000) && k + 1 < ins.size() &&
        (ins[k + 1][0] & 0x7FF) == kOpLt) {
      const uint32_t* q = p + 1;
      const uint32_t* e = p + l;
      uint32_t lens[3], types[3], idxs[3];
      const uint32_t* starts[3];
      bool ok = true;
      for (int j = 0; j < 3 && ok; ++j) {
        starts[j] = q;
        ok = ParseOperand(q, e, &lens[j], &types[j], &idxs[j]);
        q += ok ? lens[j] : 0;
      }
      if (ok && q == e) {
        int posI = -1, cbI = -1;
        for (int j = 1; j < 3; ++j) {
          if (types[j] == 1 && idxs[j] == posReg) posI = j;
          if (types[j] == 8) cbI = j;
        }
        if (posI > 0 && cbI > 0) {
          uint32_t bodyLen = lens[0] + lens[cbI] + 3 + lens[posI];
          o.push_back((p[0] & ~0x7F0007FFu) | kOpMad | ((1 + bodyLen) << 24));
          o.insert(o.end(), starts[0], starts[0] + lens[0]);
          o.insert(o.end(), starts[cbI], starts[cbI] + lens[cbI]);
          o.insert(o.end(), kScaleOperand, kScaleOperand + 3);
          o.insert(o.end(), starts[posI], starts[posI] + lens[posI]);
          ++*changed;
          continue;
        }
      }
    }
    o.insert(o.end(), p, p + l);
  }
  if (!*changed) return false;
  o[1] = (uint32_t)o.size();

  uint32_t newSize = (uint32_t)o.size() * 4;
  int32_t delta = (int32_t)newSize - (int32_t)ssize;
  out.assign(b, b + so + 4);
  out.insert(out.end(), reinterpret_cast<uint8_t*>(&newSize),
             reinterpret_cast<uint8_t*>(&newSize) + 4);
  out.insert(out.end(), reinterpret_cast<uint8_t*>(o.data()),
             reinterpret_cast<uint8_t*>(o.data()) + newSize);
  out.insert(out.end(), b + so + 8 + ssize, b + len);
  uint32_t newTotal = (uint32_t)out.size();
  memcpy(out.data() + 24, &newTotal, 4);
  for (uint32_t c = 0; c < nch; ++c) {
    uint32_t off;
    memcpy(&off, out.data() + 32 + 4 * c, 4);
    if (off > so) {
      off += delta;
      memcpy(out.data() + 32 + 4 * c, &off, 4);
    }
  }
  DxbcChecksum(out);
  return true;
}

// ---------------------------------------------------------------- hooks
using CreatePSFn = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const void*, SIZE_T,
                                                ID3D11ClassLinkage*, ID3D11PixelShader**);
using PSSetShaderFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11PixelShader*,
                                                ID3D11ClassInstance* const*, UINT);
using PSSetCBFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT,
                                            ID3D11Buffer* const*);
using CreateWindowExAFn = HWND(WINAPI*)(DWORD, LPCSTR, LPCSTR, DWORD, int, int, int, int, HWND,
                                        HMENU, HINSTANCE, LPVOID);


constexpr int kVtCreatePS = 15;    // ID3D11Device::CreatePixelShader
constexpr int kVtPSSetShader = 9;  // ID3D11DeviceContext::PSSetShader
constexpr int kVtPSSetCB = 16;     // ID3D11DeviceContext::PSSetConstantBuffers

CreatePSFn g_origCreatePS = nullptr;
PSSetShaderFn g_origPSSetShader[2] = {};
PSSetCBFn g_PSSetCB[2] = {};
void** g_ctxVt[2] = {};
CreateWindowExAFn g_origCreateWindowExA = nullptr;

CRITICAL_SECTION g_cs;
std::unordered_set<void*> g_patched;
ID3D11Buffer* volatile g_scaleBuf = nullptr;
volatile float g_bufScale = -1.0f;
int g_patchedCount = 0;

ID3D11Buffer* ScaleBuffer(ID3D11Device* dev) {
  float s = EffectiveScale();
  EnterCriticalSection(&g_cs);
  if (!g_scaleBuf || g_bufScale != s) {
    float data[4] = {s, s, s, s};
    D3D11_BUFFER_DESC d = {};
    d.ByteWidth = 16;
    d.Usage = D3D11_USAGE_IMMUTABLE;
    d.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    D3D11_SUBRESOURCE_DATA init = {data, 0, 0};
    ID3D11Buffer* buf = nullptr;
    if (SUCCEEDED(dev->CreateBuffer(&d, &init, &buf))) {
      // The previous buffer may still be referenced by recorded command lists;
      // scale changes are rare, so it is intentionally kept alive.
      g_scaleBuf = buf;
      g_bufScale = s;
    }
  }
  ID3D11Buffer* r = g_scaleBuf;
  LeaveCriticalSection(&g_cs);
  return r;
}

HRESULT STDMETHODCALLTYPE HookCreatePS(ID3D11Device* dev, const void* bc, SIZE_T len,
                                       ID3D11ClassLinkage* link, ID3D11PixelShader** out) {
  std::vector<uint8_t> patched;
  int changed = 0;
  if (IsActive() && bc && PatchClipShader(bc, len, patched, &changed)) {
    HRESULT hr = g_origCreatePS(dev, patched.data(), patched.size(), link, out);
    if (SUCCEEDED(hr) && out && *out) {
      EnterCriticalSection(&g_cs);
      g_patched.insert(*out);
      int n = ++g_patchedCount;
      LeaveCriticalSection(&g_cs);
      if (n <= 3 || n % 10 == 0)
        Log("patched UI clip pixel shader #%d (%d clip instructions)", n, changed);
      return hr;
    }
    Log("patched shader rejected (0x%08lx), using original", (unsigned long)hr);
  }
  return g_origCreatePS(dev, bc, len, link, out);
}

template <int W>
void STDMETHODCALLTYPE HookPSSetShader(ID3D11DeviceContext* ctx, ID3D11PixelShader* ps,
                                       ID3D11ClassInstance* const* ci, UINT n) {
  g_origPSSetShader[W](ctx, ps, ci, n);
  if (!ps) return;
  EnterCriticalSection(&g_cs);
  bool ours = g_patched.count(ps) != 0;
  LeaveCriticalSection(&g_cs);
  if (!ours) return;
  ID3D11Buffer* buf = g_scaleBuf;
  if (!buf || g_bufScale != EffectiveScale()) {
    ID3D11Device* dev = nullptr;
    ctx->GetDevice(&dev);
    if (dev) {
      buf = ScaleBuffer(dev);
      dev->Release();
    }
  }
  if (buf) g_PSSetCB[W](ctx, kScaleSlot, 1, &buf);
}

void PatchVt(void** vt, int idx, void* fn, void** orig) {
  DWORD old;
  VirtualProtect(&vt[idx], sizeof(void*), PAGE_READWRITE, &old);
  if (orig) *orig = vt[idx];
  vt[idx] = fn;
  VirtualProtect(&vt[idx], sizeof(void*), old, &old);
}

void HookContextVt(ID3D11DeviceContext* ctx) {
  void** vt = *reinterpret_cast<void***>(ctx);
  if (g_ctxVt[0] == vt || g_ctxVt[1] == vt) return;
  int slot = g_ctxVt[0] ? 1 : 0;
  if (g_ctxVt[slot]) return;
  g_ctxVt[slot] = vt;
  g_PSSetCB[slot] = reinterpret_cast<PSSetCBFn>(vt[kVtPSSetCB]);
  PatchVt(vt, kVtPSSetShader,
          slot == 0 ? (void*)&HookPSSetShader<0> : (void*)&HookPSSetShader<1>,
          reinterpret_cast<void**>(&g_origPSSetShader[slot]));
}

bool g_vtHooked = false;

// DXVK shares one method table per class, so hooking the tables of a throwaway
// device covers the game's device and contexts too.
void HookD3DVtables() {
  if (g_vtHooked) return;
  g_vtHooked = true;
  HMODULE d3d = GetModuleHandleA("d3d11.dll");
  using CreateDeviceFn =
      HRESULT(WINAPI*)(IDXGIAdapter*, D3D_DRIVER_TYPE, HMODULE, UINT, const D3D_FEATURE_LEVEL*,
                       UINT, UINT, ID3D11Device**, D3D_FEATURE_LEVEL*, ID3D11DeviceContext**);
  auto create = reinterpret_cast<CreateDeviceFn>(
      d3d ? GetProcAddress(d3d, "D3D11CreateDevice") : nullptr);
  if (!create) {
    Log("d3d11 not available - clip fix disabled");
    return;
  }
  ID3D11Device* dev = nullptr;
  ID3D11DeviceContext* imm = nullptr;
  HRESULT hr = create(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                      D3D11_SDK_VERSION, &dev, nullptr, &imm);
  if (FAILED(hr) || !dev) {
    Log("helper D3D11 device failed (0x%08lx) - clip fix disabled", (unsigned long)hr);
    return;
  }
  PatchVt(*reinterpret_cast<void***>(dev), kVtCreatePS, (void*)&HookCreatePS,
          reinterpret_cast<void**>(&g_origCreatePS));

  if (imm) HookContextVt(imm);
  ID3D11DeviceContext* def = nullptr;
  if (SUCCEEDED(dev->CreateDeferredContext(0, &def)) && def) {
    HookContextVt(def);
    def->Release();
  }
  if (imm) imm->Release();
  dev->Release();
  Log("D3D11 hooks installed (CreatePixelShader, PSSetShader x%d)", g_ctxVt[1] ? 2 : 1);
}

// The game creates its window before its D3D device: install the D3D hooks then.
HWND WINAPI HookCreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR name, DWORD style, int x, int y,
                                int w, int h, HWND parent, HMENU menu, HINSTANCE inst,
                                LPVOID param) {
  if (IsActive()) HookD3DVtables();
  return g_origCreateWindowExA(ex, cls, name, style, x, y, w, h, parent, menu, inst, param);
}
}  // namespace

void InstallD3DHooks() {
  InitializeCriticalSection(&g_cs);
  g_origCreateWindowExA = reinterpret_cast<CreateWindowExAFn>(PatchIAT(
      GetModuleHandleW(nullptr), "USER32.dll", "CreateWindowExA", (void*)&HookCreateWindowExA));
  Log("CreateWindowExA hook %s", g_origCreateWindowExA ? "ok" : "FAILED");
}

// Fallback (first frame) in case no window was created after our hooks.
void HookD3DVtablesOnce() {
  if (IsActive()) HookD3DVtables();
}
}  // namespace uis
