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
#include <utility>
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

void RebuildDxbc(const uint8_t* b, size_t len, uint32_t so, uint32_t ssize, uint32_t nch,
                 std::vector<uint32_t>& o, std::vector<uint8_t>& out);

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
  RebuildDxbc(b, len, so, ssize, nch, o, out);
  return true;
}

// Replaces the SHEX/SHDR token stream of a DXBC container and fixes sizes, chunk
// offsets and the checksum.
void RebuildDxbc(const uint8_t* b, size_t len, uint32_t so, uint32_t ssize, uint32_t nch,
                 std::vector<uint32_t>& o, std::vector<uint8_t>& out) {
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
}

// Locates the shader token stream. Returns false if bc is not a DXBC pixel shader.
bool FindShex(const uint8_t* b, size_t len, uint32_t* so, uint32_t* ssize, uint32_t* nch) {
  if (len < 32 || memcmp(b, "DXBC", 4) != 0) return false;
  uint32_t total;
  memcpy(&total, b + 24, 4);
  memcpy(nch, b + 28, 4);
  if (total != len || 32 + 4 * *nch > len) return false;
  *so = 0;
  for (uint32_t c = 0; c < *nch; ++c) {
    uint32_t o;
    memcpy(&o, b + 32 + 4 * c, 4);
    if (o + 8 <= len && (!memcmp(b + o, "SHEX", 4) || !memcmp(b + o, "SHDR", 4))) {
      *so = o;
      memcpy(ssize, b + o + 4, 4);
    }
  }
  if (!*so || *so + 8 + *ssize > len || *ssize < 8) return false;
  uint32_t ver;
  memcpy(&ver, b + *so + 8, 4);
  return (ver >> 16) == 0;  // pixel shader
}

// ------------------------------------------------- hi-res tile sprites
// Civ V draws "-index" tile sprites with a shader that fetches exact 1x texel centres:
//     mul  R.xy, R.xyxx, cbT.x        ; (tile + frac) * tileSize
//     round_ni R.xy, R.xyxx
//     add  R.xy, R.xyxx, l(0.5, 0.5)
//     div  R.xy, R.xyxx, cbD.xyxx     ; / data texture size (from the file)
//     sample_l ..., R.xyxx, tN, ...
// so a k x data texture would only ever be sampled at its 1x positions. Each such
// sequence is rewritten to use the texture's real size:
//     resinfo rX.xyzw, l(0), tN.xyzw  ; rX.xy = real size
//     div  rX.z, rX.x, cbD.x          ; k = real / file width
//     mul  rX.z, rX.z, cbT.x          ; tileSize * k
//     mul  R.xy, R.xyxx, rX.z  ...  div R.xy, R.xyxx, rX.xyxx
// With an unreplaced texture k = 1 and the result is unchanged.
constexpr uint32_t kOpDiv = 14, kOpMul = 56, kOpSampleL = 72, kOpResinfo = 61,
                   kOpDclTemps = 104, kOpRoundNi = 65, kOpMin = 51, kOpMax = 52, kOpAddI = 0,
                   kOpNe = 57, kOpOr = 60, kOpMovc = 55;

// Operand reads one component: select1 (.x) or a replicated swizzle (.xxxx).
bool IsScalarSel(uint32_t tok) {
  uint32_t mode = (tok >> 2) & 3, sw = (tok >> 4) & 0xFF;
  return mode == 2 || (mode == 1 && (sw == 0x00 || sw == 0x55 || sw == 0xAA || sw == 0xFF));
}

bool PatchSpriteShader(const void* bc, SIZE_T len, std::vector<uint8_t>& out, int* changed) {
  auto b = static_cast<const uint8_t*>(bc);
  uint32_t so = 0, ssize = 0, nch = 0;
  if (!FindShex(b, len, &so, &ssize, &nch)) return false;
  const uint32_t* toks = reinterpret_cast<const uint32_t*>(b + so + 8);
  const uint32_t* end = toks + ssize / 4;
  struct Ins {
    const uint32_t* p;
    uint32_t op, len, nops;
    const uint32_t* ops[4];
    uint32_t olen[4], otype[4], oidx[4];
  };
  std::vector<Ins> ins;
  for (const uint32_t* p = toks + 2; p < end;) {
    uint32_t l = InstrLen(p);
    if (!l || p + l > end) return false;
    Ins in = {p, p[0] & 0x7FF, l, 0, {}, {}, {}, {}};
    const uint32_t* q = p + 1;
    // skip extended opcode tokens
    if (p[0] & 0x80000000u) {
      while (q < p + l && (*q & 0x80000000u)) ++q;
      ++q;
    }
    if (in.op < 88 || in.op > 106) {  // not a declaration: parse operands
      while (q < p + l && in.nops < 4) {
        uint32_t ol, ty, idx;
        if (!ParseOperand(q, p + l, &ol, &ty, &idx)) break;
        in.ops[in.nops] = q;
        in.olen[in.nops] = ol;
        in.otype[in.nops] = ty;
        in.oidx[in.nops] = idx;
        ++in.nops;
        q += ol;
      }
    }
    ins.push_back(in);
    p += l;
  }
  // temp register count
  int tempsAt = -1;
  for (size_t k = 0; k < ins.size(); ++k)
    if (ins[k].op == kOpDclTemps && ins[k].len == 2) tempsAt = (int)k;
  if (tempsAt < 0) return false;
  uint32_t rX = ins[tempsAt].p[1];

  // find (mul ... div, sample_l) sequences
  struct Hit {
    size_t mul, div;
    uint32_t res;
    bool smooth;  // "round_ni; add 0.5" between mul and div: replace by a clamp
    // index-map lookup of the same block ("mul R.xy, vUV.xyxx, cbI.xyxx" + sample_l tI):
    // used for the direct mode (whole hi-res image when the index map is a stand-in)
    const uint32_t* uv;
    uint32_t uvLen;
    const uint32_t* cbI;
    uint32_t idxRes;
  };
  std::vector<Hit> hits;
  for (size_t k = 1; k < ins.size(); ++k) {
    const Ins& s = ins[k];
    if (s.op != kOpSampleL || s.nops < 3 || s.otype[2] != 7) continue;
    const Ins& d = ins[k - 1];
    if (d.op != kOpDiv || d.nops != 3 || d.otype[0] != 0 || d.otype[1] != 0 || d.otype[2] != 8)
      continue;
    // the cb operand must be a plain cbN[M] (2D immediate index)
    if (((d.ops[2][0] >> 20) & 3) != 2 || (d.ops[2][0] & 0x80000000u) || d.olen[2] != 3) continue;
    for (size_t j = k - 1; j-- > 0 && j + 6 >= k;) {
      const Ins& m = ins[j];
      // (tile + frac) is a temp, the tile size a scalar cb component (.x); this rules
      // out the index-map lookup (mul R.xy, v0.xyxx, cbI.xyxx)
      if (m.op == kOpMul && m.nops == 3 && m.otype[0] == 0 && m.oidx[0] == d.oidx[0] &&
          m.otype[1] == 0 && m.otype[2] == 8 && m.olen[2] == 3 &&
          !(m.ops[2][0] & 0x80000000u) && IsScalarSel(m.ops[2][0])) {
        bool sm = j + 3 == k - 1 && ins[j + 1].op == kOpRoundNi && ins[j + 2].op == kOpAddI;
        Hit hit = {j, k - 1, s.oidx[2], sm, nullptr, 0, nullptr, 0};
        // index lookup: the last "mul R, v.xyxx, cb.xyxx" before an index sample_l
        for (size_t q = j; q-- > 0 && q + 60 >= j;) {
          const Ins& im = ins[q];
          if (im.op == kOpMul && im.nops == 3 && im.otype[1] == 1 && im.otype[2] == 8 &&
              im.olen[2] == 3 && !(im.ops[2][0] & 0x80000000u) && im.olen[1] <= 3) {
            for (size_t r = q + 1; r < j; ++r)
              if (ins[r].op == kOpSampleL && ins[r].nops >= 3 && ins[r].otype[2] == 7 &&
                  ins[r].oidx[2] != s.oidx[2]) {
                hit.uv = im.ops[1];
                hit.uvLen = im.olen[1];
                hit.cbI = im.ops[2];
                hit.idxRes = ins[r].oidx[2];
                break;
              }
            break;
          }
        }
        hits.push_back(hit);
        break;
      }
    }
  }
  if (hits.empty()) {
    // Looks like a tile-sprite shader (index lookup + rounding) but the pattern did not
    // match: save it so the variant can be added.
    int rounds = 0, samplesL = 0;
    for (const Ins& in : ins) {
      rounds += in.op == 65;  // round_ni
      samplesL += in.op == kOpSampleL;
    }
    if (rounds >= 2 && samplesL >= 2) {
      static int n = 0;
      std::wstring dir = g_modDir + L"\\shaders\\";
      CreateDirectoryW(dir.c_str(), nullptr);
      wchar_t nm[64];
      swprintf(nm, 64, L"unmatched_%02d.dxbc", n++);
      if (FILE* f = _wfopen((dir + nm).c_str(), L"wb")) {
        fwrite(bc, 1, len, f);
        fclose(f);
      }
      Log("tile-sprite-like shader not patched (saved %ls)", nm);
    }
    return false;
  }

  std::vector<uint32_t> o(toks, toks + 2);
  size_t h = 0;
  for (size_t k = 0; k < ins.size(); ++k) {
    const Ins& in = ins[k];
    if ((int)k == tempsAt) {
      o.push_back(in.p[0]);
      o.push_back(rX + 2);  // rX: real size / tile size, rX+1: tile clamp
      continue;
    }
    // smooth sampling: drop "round_ni R; add R, 0.5" of a smooth hit (re-emitted below)
    bool skip = false;
    for (const Hit& hh : hits)
      if (hh.smooth && (k == hh.mul + 1 || k == hh.mul + 2)) skip = true;
    if (skip) continue;
    if (h < hits.size() && k == hits[h].mul) {
      const Ins& d = ins[hits[h].div];
      uint32_t cbSlot = d.ops[2][1], cbElem = d.ops[2][2];
      // resinfo rX.xyzw, l(0), tN.xyzw
      o.insert(o.end(), {kOpResinfo | (7u << 24), 0x001000F2u, rX, 0x00004001u, 0u, 0x00107E46u,
                         hits[h].res});
      // div rX.z, rX.x, cbD.x
      o.insert(o.end(), {kOpDiv | (8u << 24), 0x00100042u, rX, 0x0010000Au, rX, 0x0020800Au,
                         cbSlot, cbElem});
      // mul rX.z, rX.z, cbT.x  (cbT operand copied as is)
      o.insert(o.end(), {kOpMul | (8u << 24), 0x00100042u, rX, 0x0010002Au, rX});
      o.insert(o.end(), in.ops[2], in.ops[2] + 3);
      // mul R.xy, R.xyxx, rX.z
      uint32_t bodyLen = in.olen[0] + in.olen[1] + 2;
      o.push_back((in.p[0] & ~0x7F000000u) | ((1 + bodyLen) << 24));
      o.insert(o.end(), in.ops[0], in.ops[0] + in.olen[0]);
      o.insert(o.end(), in.ops[1], in.ops[1] + in.olen[1]);
      o.insert(o.end(), {0x0010002Au, rX});
      if (hits[h].smooth) {
        // Filter inside the tile instead of fetching the nearest texel:
        //   rY.xy = floor(R / T) * T            tile origin
        //   R = max(R, origin + 0.5); R = min(R, origin + T - 0.5)
        // At integer ratios the sample points are texel centres (unchanged look).
        uint32_t rY = rX + 1;
        const uint32_t* dR = in.ops[0];  // R.xy (dest)
        const uint32_t* sR = in.ops[1];  // R.xyxx (source)
        uint32_t ld = in.olen[0], ls = in.olen[1];
        // div rY.xy, R.xyxx, rX.zzzz
        o.push_back(kOpDiv | ((1 + 2 + ls + 2) << 24));
        o.insert(o.end(), {0x00100032u, rY});
        o.insert(o.end(), sR, sR + ls);
        o.insert(o.end(), {0x00100AA6u, rX});
        // round_ni rY.xy, rY.xyxx
        o.insert(o.end(), {kOpRoundNi | (5u << 24), 0x00100032u, rY, 0x00100046u, rY});
        // mul rY.xy, rY.xyxx, rX.zzzz
        o.insert(o.end(), {kOpMul | (7u << 24), 0x00100032u, rY, 0x00100046u, rY, 0x00100AA6u, rX});
        // add rY.zw, rY.xxxy, l(0.5)
        uint32_t half = 0x3F000000u, mhalf = 0xBF000000u;
        o.insert(o.end(), {kOpAddI | (10u << 24), 0x001000C2u, rY, 0x00100406u, rY, 0x00004002u,
                           half, half, half, half});
        // max R.xy, R.xyxx, rY.zwzz
        o.push_back(kOpMax | ((1 + ld + ls + 2) << 24));
        o.insert(o.end(), dR, dR + ld);
        o.insert(o.end(), sR, sR + ls);
        o.insert(o.end(), {0x00100AE6u, rY});
        // add rY.xy, rY.xyxx, rX.zzzz ; add rY.xy, rY.xyxx, l(-0.5)
        o.insert(o.end(), {kOpAddI | (7u << 24), 0x00100032u, rY, 0x00100046u, rY, 0x00100AA6u, rX});
        o.insert(o.end(), {kOpAddI | (10u << 24), 0x00100032u, rY, 0x00100046u, rY, 0x00004002u,
                           mhalf, mhalf, mhalf, mhalf});
        // min R.xy, R.xyxx, rY.xyxx
        o.push_back(kOpMin | ((1 + ld + ls + 2) << 24));
        o.insert(o.end(), dR, dR + ld);
        o.insert(o.end(), sR, sR + ls);
        o.insert(o.end(), {0x00100046u, rY});
      }
      continue;
    }
    if (h < hits.size() && k == hits[h].div) {
      // div R.xy, R.xyxx, rX.xyxx
      uint32_t bodyLen = in.olen[0] + in.olen[1] + 2;
      o.push_back((in.p[0] & ~0x7F000000u) | ((1 + bodyLen) << 24));
      o.insert(o.end(), in.ops[0], in.ops[0] + in.olen[0]);
      o.insert(o.end(), in.ops[1], in.ops[1] + in.olen[1]);
      o.insert(o.end(), {0x00100046u, rX});
      if (hits[h].uv) {
        // Direct mode: if the bound index map is not the size the engine expects (our 1x1
        // stand-in), the data texture is the whole hi-res image: sample it at the UV.
        uint32_t rY = rX + 1;
        o.insert(o.end(), {kOpResinfo | (7u << 24), 0x001000F2u, rY, 0x00004001u, 0u, 0x00107E46u,
                           hits[h].idxRes});
        o.insert(o.end(), {kOpNe | (8u << 24), 0x00100032u, rY, 0x00100046u, rY});
        o.insert(o.end(), hits[h].cbI, hits[h].cbI + 3);
        o.insert(o.end(), {kOpOr | (7u << 24), 0x00100012u, rY, 0x0010000Au, rY, 0x0010001Au, rY});
        o.push_back(kOpMovc | ((1 + in.olen[0] + 2 + hits[h].uvLen + in.olen[1]) << 24));
        o.insert(o.end(), in.ops[0], in.ops[0] + in.olen[0]);
        o.insert(o.end(), {0x00100006u, rY});
        o.insert(o.end(), hits[h].uv, hits[h].uv + hits[h].uvLen);
        o.insert(o.end(), in.ops[1], in.ops[1] + in.olen[1]);
      }
      ++h;
      continue;
    }
    o.insert(o.end(), in.p, in.p + in.len);
  }
  *changed = (int)hits.size();
  RebuildDxbc(b, len, so, ssize, nch, o, out);
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


// ------------------------------------------------- AI-upscaled UI art
// tools/uiart.py writes UIArt/x<k>/uiart.txt: <fnv64 mip0> <w> <h> <k> <file>.
// Textures uploaded with matching pixels are replaced by the k x version; the
// UI samples with normalized UVs, so the layout is unchanged.
using CreateTex2DFn = HRESULT(STDMETHODCALLTYPE*)(ID3D11Device*, const D3D11_TEXTURE2D_DESC*,
                                                   const D3D11_SUBRESOURCE_DATA*,
                                                   ID3D11Texture2D**);
constexpr int kVtCreateTex2D = 5;  // ID3D11Device::CreateTexture2D
CreateTex2DFn g_origCreateTex2D = nullptr;

struct ArtEntry {
  uint32_t w, h, k;
  std::wstring file;
};
std::unordered_map<uint64_t, ArtEntry> g_art;
std::unordered_set<uint64_t> g_artSizes;  // (w << 32 | h) quick reject
int g_artReplaced = 0, g_artMisses = 0;

void LoadArtManifest(const wchar_t* k) {
  std::wstring dir = g_modDir + L"\\UIArt\\" + k + L"\\";
  FILE* f = _wfopen((dir + L"uiart.txt").c_str(), L"r");
  if (!f) return;
  char hash[32], file[260];
  unsigned w, h, kk;
  while (fscanf(f, "%31s %u %u %u %259s", hash, &w, &h, &kk, file) == 5) {
    wchar_t wf[260];
    MultiByteToWideChar(CP_UTF8, 0, file, -1, wf, 260);
    g_art[strtoull(hash, nullptr, 16)] = ArtEntry{w, h, kk, dir + wf};
    g_artSizes.insert(((uint64_t)w << 32) | h);
  }
  fclose(f);
  Log("AI-upscaled UI art: %u textures (%ls)", (unsigned)g_art.size(), k);
}

// 32-bit formats the engine uses for UI art. The DX11 renderer uploads the (BGRA) DDS
// files as R8G8B8A8_TYPELESS, i.e. with red and blue swapped.
bool IsBGRA(DXGI_FORMAT f) {
  return f == DXGI_FORMAT_B8G8R8A8_UNORM || f == DXGI_FORMAT_B8G8R8A8_TYPELESS ||
         f == DXGI_FORMAT_B8G8R8A8_UNORM_SRGB;
}
bool IsRGBA(DXGI_FORMAT f) {
  return f == DXGI_FORMAT_R8G8B8A8_TYPELESS || f == DXGI_FORMAT_R8G8B8A8_UNORM ||
         f == DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;
}

// Block-compressed UI art (BC1 = DXT1, BC3 = DXT5): bytes per 4x4 block, 0 otherwise.
UINT BCBlockBytes(DXGI_FORMAT f) {
  switch (f) {
    case DXGI_FORMAT_BC1_TYPELESS: case DXGI_FORMAT_BC1_UNORM: case DXGI_FORMAT_BC1_UNORM_SRGB:
      return 8;
    case DXGI_FORMAT_BC3_TYPELESS: case DXGI_FORMAT_BC3_UNORM: case DXGI_FORMAT_BC3_UNORM_SRGB:
      return 16;
    default:
      return 0;
  }
}

// Index maps of "-index" tile sprites (R8 / R16): replaced by a 1x1 stand-in when the
// sprite's data texture is replaced by the whole hi-res image ("null" in the manifest).
UINT IndexBytes(DXGI_FORMAT f) {
  switch (f) {
    case DXGI_FORMAT_R8_TYPELESS: case DXGI_FORMAT_R8_UNORM: case DXGI_FORMAT_R8_UINT:
      return 1;
    case DXGI_FORMAT_R16_TYPELESS: case DXGI_FORMAT_R16_UNORM: case DXGI_FORMAT_R16_UINT:
      return 2;
    default:
      return 0;
  }
}

// FNV-1a over mip 0 in the DDS file's byte order (BGRA), whatever the upload order.
// Diagnostics: which render target is each replaced texture drawn into?
std::unordered_map<void*, std::wstring> g_artTex;  // replaced texture -> file name
std::unordered_set<std::wstring> g_artBoundLogged;

uint64_t HashMip0(const D3D11_TEXTURE2D_DESC* d, const D3D11_SUBRESOURCE_DATA* init) {
  uint64_t h = 0xCBF29CE484222325ull;
  auto p = static_cast<const uint8_t*>(init->pSysMem);
  if (UINT ib = IndexBytes(d->Format)) {  // index maps: raw rows
    for (UINT y = 0; y < d->Height; ++y) {
      const uint8_t* r = p + (size_t)y * init->SysMemPitch;
      for (size_t x = 0; x < (size_t)d->Width * ib; ++x) h = (h ^ r[x]) * 0x100000001B3ull;
    }
    return h;
  }
  if (UINT bs = BCBlockBytes(d->Format)) {  // raw blocks, row by block row
    UINT bw = (d->Width + 3) / 4, bh = (d->Height + 3) / 4;
    for (UINT y = 0; y < bh; ++y) {
      const uint8_t* r = p + (size_t)y * init->SysMemPitch;
      for (size_t x = 0; x < (size_t)bw * bs; ++x) h = (h ^ r[x]) * 0x100000001B3ull;
    }
    return h;
  }
  bool swap = IsRGBA(d->Format);
  for (UINT y = 0; y < d->Height; ++y) {
    const uint8_t* r = p + (size_t)y * init->SysMemPitch;
    for (UINT x = 0; x < d->Width; ++x, r += 4) {
      uint8_t px[4] = {r[swap ? 2 : 0], r[1], r[swap ? 0 : 2], r[3]};
      for (uint8_t b : px) h = (h ^ b) * 0x100000001B3ull;
    }
  }
  return h;
}

// DumpTextures=1: write mip 0 of every distinct uncompressed upload to UIScale/dump as
// <fmt>_<w>x<h>_<fnv64>.bin (tightly packed rows), to work out how the engine builds
// the textures it uploads (e.g. the "-index" tile sprites).
int BytesPerPixel(DXGI_FORMAT f) {
  if (IsBGRA(f) || IsRGBA(f)) return 4;
  switch (f) {
    case DXGI_FORMAT_R8_TYPELESS: case DXGI_FORMAT_R8_UNORM: case DXGI_FORMAT_R8_UINT:
    case DXGI_FORMAT_A8_UNORM:
      return 1;
    case DXGI_FORMAT_R16_TYPELESS: case DXGI_FORMAT_R16_UNORM: case DXGI_FORMAT_R16_UINT:
    case DXGI_FORMAT_R8G8_UNORM: case DXGI_FORMAT_R8G8_TYPELESS: case DXGI_FORMAT_B5G6R5_UNORM:
    case DXGI_FORMAT_B5G5R5A1_UNORM:
      return 2;
    default:
      return 0;
  }
}
std::unordered_set<uint64_t> g_dumped;

void DumpTexture(const D3D11_TEXTURE2D_DESC* d, const D3D11_SUBRESOURCE_DATA* init) {
  int bpp = BytesPerPixel(d->Format);
  if (!bpp || g_dumped.size() >= 6000) return;
  size_t row = (size_t)d->Width * bpp;
  std::vector<uint8_t> px(row * d->Height);
  for (UINT y = 0; y < d->Height; ++y)
    memcpy(&px[y * row], static_cast<const uint8_t*>(init->pSysMem) + (size_t)y * init->SysMemPitch,
           row);
  uint64_t h = 0xCBF29CE484222325ull;
  for (uint8_t b : px) h = (h ^ b) * 0x100000001B3ull;
  if (!g_dumped.insert(h).second) return;
  static std::wstring dir;
  if (dir.empty()) {
    dir = g_modDir + L"\\dump\\";
    CreateDirectoryW(dir.c_str(), nullptr);
  }
  wchar_t name[96];
  swprintf(name, 96, L"%d_%ux%u_m%u_%016llx.bin", (int)d->Format, d->Width, d->Height,
           d->MipLevels, (unsigned long long)h);
  if (FILE* f = _wfopen((dir + name).c_str(), L"wb")) {
    fwrite(px.data(), 1, px.size(), f);
    fclose(f);
  }
}

// ------------------------------------------------- map overlay pages (experimental)
// The engine paints borders / fog into square RGBA render targets with auto-generated
// mips (205 x 256^2, 1024^2, 2048^2) that are then projected onto the terrain. At their
// fixed resolution the border lines are visibly stair-stepped when zoomed in. With
// OverlayScale=N they are created N x larger and every viewport / scissor rect used
// while one is bound is scaled by N; sampling uses normalized UVs, so nothing else moves.
std::unordered_set<void*> g_overlayRT;
SRWLOCK g_overlayLock = SRWLOCK_INIT;
bool IsOverlayRT(const D3D11_TEXTURE2D_DESC* d);
bool DescOf(ID3D11Resource* r, D3D11_TEXTURE2D_DESC* d);
// Only the 256^2 map-tile pages are scaled: they are drawn without a depth buffer (two
// pages at a time). The 1024^2 / 2048^2 pages (fog / map wide, the latter with its own
// depth buffer) stay as they are.
bool IsScaledOverlay(const D3D11_TEXTURE2D_DESC* d) {
  return IsOverlayRT(d) && d->Width == 256;
}
bool IsOverlayRT(const D3D11_TEXTURE2D_DESC* d) {
  return d->Width == d->Height && (d->Width == 256 || d->Width == 1024 || d->Width == 2048) &&
         d->ArraySize == 1 && d->Format == DXGI_FORMAT_R8G8B8A8_TYPELESS &&
         d->BindFlags == (D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE) &&
         (d->MiscFlags & D3D11_RESOURCE_MISC_GENERATE_MIPS);
}

HRESULT STDMETHODCALLTYPE HookCreateTex2D(ID3D11Device* dev, const D3D11_TEXTURE2D_DESC* d,
                                          const D3D11_SUBRESOURCE_DATA* init,
                                          ID3D11Texture2D** out) {
  if (g_cfg.dumpPixels && d && init && init->pSysMem && d->ArraySize == 1) DumpTexture(d, init);
  if (g_cfg.overlayScale && d && !init && IsScaledOverlay(d)) {
    D3D11_TEXTURE2D_DESC sd = *d;
    UINT f = (UINT)g_cfg.overlayScale;
    sd.Width *= f;
    sd.Height *= f;
    if (sd.MipLevels) sd.MipLevels += f == 2 ? 1 : 2;
    HRESULT hr = g_origCreateTex2D(dev, &sd, nullptr, out);
    if (SUCCEEDED(hr) && out && *out) {
      AcquireSRWLockExclusive(&g_overlayLock);
      g_overlayRT.insert(*out);
      size_t n = g_overlayRT.size();
      ReleaseSRWLockExclusive(&g_overlayLock);
      if (n <= 3 || n % 50 == 0)
        Log("overlay RT #%u %ux%u -> %ux%u", (unsigned)n, d->Width, d->Height, sd.Width,
            sd.Height);
      return hr;
    }
    Log("overlay RT scaling failed (0x%08lx), using original size", (unsigned long)hr);
  }
  if (d && init && init->pSysMem && out && d->ArraySize == 1 &&
      (IsBGRA(d->Format) || IsRGBA(d->Format) || BCBlockBytes(d->Format) ||
       IndexBytes(d->Format)) &&
      g_artSizes.count(((uint64_t)d->Width << 32) | d->Height)) {
    uint64_t h = HashMip0(d, init);
    auto it = g_art.find(h);
    if (it != g_art.end() && it->second.file.size() >= 4 &&
        it->second.file.compare(it->second.file.size() - 4, 4, L"null") == 0) {
      // 1x1 stand-in index map: the patched sprite shader then samples the (whole,
      // hi-res) data texture directly
      D3D11_TEXTURE2D_DESC sd = *d;
      sd.Width = sd.Height = 1;
      sd.MipLevels = 1;
      uint32_t zero = 0;
      D3D11_SUBRESOURCE_DATA one = {&zero, 4, 0};
      HRESULT hr = g_origCreateTex2D(dev, &sd, &one, out);
      if (SUCCEEDED(hr)) {
        if (++g_artReplaced <= 200 || g_artReplaced % 100 == 0)
          Log("UI art replaced #%d: %ux%u index map -> 1x1 stand-in", g_artReplaced, d->Width,
              d->Height);
        return hr;
      }
      Log("index stand-in creation failed 0x%08lx", (unsigned long)hr);
      return g_origCreateTex2D(dev, d, init, out);
    }
    if (it == g_art.end()) {
      if (g_artMisses++ < 40) {
        auto p = static_cast<const uint8_t*>(init->pSysMem);
        Log("UI art candidate %ux%u fmt %d hash %016llx: no match (px0 %02x%02x%02x%02x)",
            d->Width, d->Height, (int)d->Format, (unsigned long long)h, p[0], p[1], p[2], p[3]);
      }
    } else {
      FILE* f = _wfopen(it->second.file.c_str(), L"rb");
      std::vector<uint8_t> buf;
      if (f) {
        fseek(f, 0, SEEK_END);
        buf.resize(ftell(f));
        fseek(f, 0, SEEK_SET);
        if (fread(buf.data(), 1, buf.size(), f) != buf.size()) buf.clear();
        fclose(f);
      }
      if (buf.size() > 128) {
        uint32_t fh, fw, fm;
        memcpy(&fh, &buf[12], 4);
        memcpy(&fw, &buf[16], 4);
        memcpy(&fm, &buf[28], 4);
        D3D11_TEXTURE2D_DESC hd = *d;
        hd.Width = fw;
        hd.Height = fh;
        UINT levels = d->MipLevels ? d->MipLevels : fm;
        if (levels > fm) levels = fm;
        hd.MipLevels = levels;
        std::vector<D3D11_SUBRESOURCE_DATA> sub(levels);
        size_t off = 128;
        UINT bs = BCBlockBytes(d->Format);
        // the file must be of the same family: BGRA for 32-bit, DXT1 / DXT5 for BC1 / BC3
        bool ok = bs ? !memcmp(&buf[84], bs == 8 ? "DXT1" : "DXT5", 4)
                     : !memcmp(&buf[84], "\0\0\0\0", 4);
        for (UINT i = 0; i < levels && ok; ++i) {
          UINT lw = fw >> i ? fw >> i : 1, lh = fh >> i ? fh >> i : 1;
          UINT pitch = bs ? ((lw + 3) / 4) * bs : lw * 4;
          size_t size = bs ? (size_t)pitch * ((lh + 3) / 4) : (size_t)lw * lh * 4;
          if (off + size > buf.size()) ok = false;
          sub[i] = {buf.data() + off, pitch, 0};
          off += size;
        }
        if (ok) {
          // files are BGRA; keep the engine's format (its views expect it) and swap instead
          if (IsRGBA(d->Format) && !bs)
            for (size_t i = 128; i + 3 < off; i += 4) std::swap(buf[i], buf[i + 2]);
          HRESULT hr = g_origCreateTex2D(dev, &hd, sub.data(), out);
          if (SUCCEEDED(hr)) {
            const std::wstring& fn = it->second.file;
            g_artTex[*out] = fn.substr(fn.rfind(L'\\') + 1);
            if (++g_artReplaced <= 200 || g_artReplaced % 100 == 0) {
              const std::wstring& f = it->second.file;
              Log("UI art replaced #%d: %ux%u -> %ux%u %ls", g_artReplaced, d->Width, d->Height,
                  fw, fh, f.c_str() + (f.rfind(L'\\') + 1));
            }
            return hr;
          }
          Log("UI art texture creation failed 0x%08lx", (unsigned long)hr);
        }
      }
    }
  }
  return g_origCreateTex2D(dev, d, init, out);
}

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

HRESULT STDMETHODCALLTYPE HookCreatePSInner(ID3D11Device*, const void*, SIZE_T,
                                            ID3D11ClassLinkage*, ID3D11PixelShader**);
std::unordered_map<void*, std::vector<uint8_t>> g_psCode;  // diagnostics: PS -> bytecode
void* g_curPS[2] = {};
std::unordered_set<void*> g_psDumped;
void DumpPSCode(void* ps, const wchar_t* tag) {
  EnterCriticalSection(&g_cs);
  auto pc = g_psCode.find(ps);
  if (pc != g_psCode.end() && g_psDumped.insert(ps).second) {
    std::wstring dir = g_modDir + L"\\shaders\\";
    CreateDirectoryW(dir.c_str(), nullptr);
    wchar_t nm[64];
    swprintf(nm, 64, L"%ls_ps_%p.dxbc", tag, ps);
    if (FILE* f = _wfopen((dir + nm).c_str(), L"wb")) {
      fwrite(pc->second.data(), 1, pc->second.size(), f);
      fclose(f);
    }
  }
  LeaveCriticalSection(&g_cs);
}

void RememberPS(ID3D11PixelShader** out, const void* bc, SIZE_T len) {
  if (!g_cfg.dumpTextures || !out || !*out || !bc) return;
  EnterCriticalSection(&g_cs);
  auto p = static_cast<const uint8_t*>(bc);
  g_psCode[*out].assign(p, p + len);
  LeaveCriticalSection(&g_cs);
}

HRESULT STDMETHODCALLTYPE HookCreatePS(ID3D11Device* dev, const void* bc, SIZE_T len,
                                       ID3D11ClassLinkage* link, ID3D11PixelShader** out) {
  HRESULT hr0 = HookCreatePSInner(dev, bc, len, link, out);
  if (SUCCEEDED(hr0)) RememberPS(out, bc, len);
  return hr0;
}

HRESULT STDMETHODCALLTYPE HookCreatePSInner(ID3D11Device* dev, const void* bc, SIZE_T len,
                                            ID3D11ClassLinkage* link, ID3D11PixelShader** out) {
  const void* origBc = bc;
  SIZE_T origLen = len;
  // 1) hi-res tile sprites (only when replacement art is loaded)
  std::vector<uint8_t> sprite;
  int nSprite = 0;
  if (!g_art.empty() && bc && PatchSpriteShader(bc, len, sprite, &nSprite)) {
    bc = sprite.data();
    len = sprite.size();
  }
  // 2) UI clip rects
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
      if (nSprite) Log("patched tile sprite shader (%d fetches)", nSprite);
      return hr;
    }
    Log("patched shader rejected (0x%08lx), using original", (unsigned long)hr);
    return g_origCreatePS(dev, origBc, origLen, link, out);
  }
  if (nSprite) {
    HRESULT hr = g_origCreatePS(dev, bc, len, link, out);
    if (SUCCEEDED(hr)) {
      Log("patched tile sprite shader (%d fetches)", nSprite);
      return hr;
    }
    Log("patched sprite shader rejected (0x%08lx), using original", (unsigned long)hr);
    return g_origCreatePS(dev, origBc, origLen, link, out);
  }
  return g_origCreatePS(dev, bc, len, link, out);
}

template <int W>
void STDMETHODCALLTYPE HookPSSetShader(ID3D11DeviceContext* ctx, ID3D11PixelShader* ps,
                                       ID3D11ClassInstance* const* ci, UINT n) {
  g_origPSSetShader[W](ctx, ps, ci, n);
  g_curPS[W] = ps;
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

constexpr int kVtPSSetSRV = 8;  // ID3D11DeviceContext::PSSetShaderResources
using PSSetSRVFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT,
                                             ID3D11ShaderResourceView* const*);
PSSetSRVFn g_origPSSetSRV[2] = {};

const std::wstring* g_boundArt[2][8] = {};
std::unordered_set<std::wstring> g_drawLogged;
constexpr int kVtDrawIndexed = 12, kVtDraw = 13;
using DrawIndexedFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT, INT);
using DrawFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, UINT);
DrawIndexedFn g_origDrawIndexed[2] = {};
DrawFn g_origDraw[2] = {};

void DumpPSCode(void* ps, const wchar_t* tag);

template <int S>
void NoteDraw() {
  for (int i = 0; i < 8; ++i) {
    const std::wstring* a = g_boundArt[S][i];
    if (!a || g_drawLogged.size() > 300) continue;
    wchar_t key[300];
    swprintf(key, 300, L"%ls@%p", a->c_str(), g_curPS[S]);
    if (!g_drawLogged.insert(key).second) continue;
    Log("draw ctx%d slot %d %ls ps %p", S, i, a->c_str(), g_curPS[S]);
    DumpPSCode(g_curPS[S], L"draw");
  }
}
template <int S>
void STDMETHODCALLTYPE HookDrawIndexed(ID3D11DeviceContext* c, UINT n, UINT i0, INT b) {
  NoteDraw<S>();
  g_origDrawIndexed[S](c, n, i0, b);
}
template <int S>
void STDMETHODCALLTYPE HookDraw(ID3D11DeviceContext* c, UINT n, UINT v0) {
  NoteDraw<S>();
  g_origDraw[S](c, n, v0);
}

template <int S>
void STDMETHODCALLTYPE HookPSSetSRV(ID3D11DeviceContext* c, UINT start, UINT n,
                                    ID3D11ShaderResourceView* const* v) {
  for (UINT i = 0; i < n && start + i < 8; ++i) {
    const std::wstring* a = nullptr;
    if (v && v[i]) {
      ID3D11Resource* r = nullptr;
      v[i]->GetResource(&r);
      if (r) {
        auto it = g_artTex.find(r);
        if (it != g_artTex.end()) a = &it->second;
        r->Release();
      }
    }
    g_boundArt[S][start + i] = a;
  }
  if (v && !g_artTex.empty() && g_artBoundLogged.size() < 150) {
    for (UINT i = 0; i < n; ++i) {
      if (!v[i]) continue;
      ID3D11Resource* r = nullptr;
      v[i]->GetResource(&r);
      if (!r) continue;
      auto it = g_artTex.find(r);
      r->Release();
      if (it == g_artTex.end() || !g_artBoundLogged.insert(it->second).second) continue;
      ID3D11RenderTargetView* rtv = nullptr;
      c->OMGetRenderTargets(1, &rtv, nullptr);
      D3D11_TEXTURE2D_DESC td = {};
      if (rtv) {
        ID3D11Resource* rr = nullptr;
        rtv->GetResource(&rr);
        D3D11_RESOURCE_DIMENSION dim;
        if (rr) {
          rr->GetType(&dim);
          if (dim == D3D11_RESOURCE_DIMENSION_TEXTURE2D)
            static_cast<ID3D11Texture2D*>(rr)->GetDesc(&td);
          rr->Release();
        }
        rtv->Release();
      }
      D3D11_VIEWPORT vp = {};
      UINT nvp = 1;
      c->RSGetViewports(&nvp, &vp);
      Log("art bound ctx%d slot %u %ls -> RT %ux%u fmt %d bind 0x%x misc 0x%x mips %u, vp %.0fx%.0f "
          "ps %p",
          S, start + i, it->second.c_str(), td.Width, td.Height, (int)td.Format, td.BindFlags,
          td.MiscFlags, td.MipLevels, vp.Width, vp.Height, g_curPS[S]);
      EnterCriticalSection(&g_cs);
      auto pc = g_psCode.find(g_curPS[S]);
      if (pc != g_psCode.end() && g_psDumped.insert(g_curPS[S]).second) {
        std::wstring dir = g_modDir + L"\\shaders\\";
        CreateDirectoryW(dir.c_str(), nullptr);
        wchar_t nm[64];
        swprintf(nm, 64, L"ps_%p.dxbc", g_curPS[S]);
        if (FILE* f = _wfopen((dir + nm).c_str(), L"wb")) {
          fwrite(pc->second.data(), 1, pc->second.size(), f);
          fclose(f);
        }
      }
      LeaveCriticalSection(&g_cs);
    }
  }
  g_origPSSetSRV[S](c, start, n, v);
}

constexpr int kVtOMSetRT = 33, kVtRSSetViewports = 44, kVtRSSetScissor = 45;
using OMSetRTFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT,
                                            ID3D11RenderTargetView* const*,
                                            ID3D11DepthStencilView*);
using RSSetVPFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, const D3D11_VIEWPORT*);
using RSSetSciFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, UINT, const D3D11_RECT*);
OMSetRTFn g_origOMSetRT[2] = {};
RSSetVPFn g_origRSSetVP[2] = {};
RSSetSciFn g_origRSSetSci[2] = {};
float g_rtScale[2] = {1.0f, 1.0f};
D3D11_VIEWPORT g_lastVP[2][4];
UINT g_lastVPn[2] = {};
D3D11_RECT g_lastSci[2][4];
UINT g_lastScin[2] = {};
int g_vpScaledLogged = 0;

template <int S>
void ApplyViewports(ID3D11DeviceContext* c) {
  D3D11_VIEWPORT v[4];
  UINT n = g_lastVPn[S];
  for (UINT i = 0; i < n; ++i) {
    v[i] = g_lastVP[S][i];
    v[i].TopLeftX *= g_rtScale[S];
    v[i].TopLeftY *= g_rtScale[S];
    v[i].Width *= g_rtScale[S];
    v[i].Height *= g_rtScale[S];
  }
  if (n) g_origRSSetVP[S](c, n, v);
  D3D11_RECT r[4];
  UINT m = g_lastScin[S];
  for (UINT i = 0; i < m; ++i) {
    r[i] = g_lastSci[S][i];
    r[i].left = (LONG)(r[i].left * g_rtScale[S]);
    r[i].top = (LONG)(r[i].top * g_rtScale[S]);
    r[i].right = (LONG)(r[i].right * g_rtScale[S]);
    r[i].bottom = (LONG)(r[i].bottom * g_rtScale[S]);
  }
  if (m) g_origRSSetSci[S](c, m, r);
}

template <int S>
void STDMETHODCALLTYPE HookOMSetRT(ID3D11DeviceContext* c, UINT n,
                                   ID3D11RenderTargetView* const* rtv,
                                   ID3D11DepthStencilView* dsv) {
  g_origOMSetRT[S](c, n, rtv, dsv);
  float sc = 1.0f;
  if (n && rtv && rtv[0]) {
    ID3D11Resource* r = nullptr;
    rtv[0]->GetResource(&r);
    if (r) {
      AcquireSRWLockShared(&g_overlayLock);
      bool known = g_overlayRT.count(r) != 0;
      ReleaseSRWLockShared(&g_overlayLock);
      if (known) {
        // guard against a freed page's address being reused by another texture
        D3D11_RESOURCE_DIMENSION dim;
        r->GetType(&dim);
        if (dim == D3D11_RESOURCE_DIMENSION_TEXTURE2D) {
          D3D11_TEXTURE2D_DESC td;
          static_cast<ID3D11Texture2D*>(r)->GetDesc(&td);
          UINT f = (UINT)g_cfg.overlayScale;
          D3D11_TEXTURE2D_DESC base = td;
          base.Width /= f;
          base.Height /= f;
          if (td.Width % f == 0 && IsScaledOverlay(&base)) sc = (float)f;
        }
      }
      r->Release();
    }
  }
  if (sc > 1.0f) {  // every target bound with a scaled page must have its size
    static int warned = 0;
    D3D11_TEXTURE2D_DESC d0 = {};
    ID3D11Resource* r0 = nullptr;
    rtv[0]->GetResource(&r0);
    if (r0) {
      DescOf(r0, &d0);
      r0->Release();
    }
    for (UINT i = 1; i < n + 1 && warned < 10; ++i) {
      ID3D11Resource* r = nullptr;
      if (i < n && rtv[i]) rtv[i]->GetResource(&r);
      if (i == n && dsv) dsv->GetResource(&r);
      D3D11_TEXTURE2D_DESC di = {};
      if (r && DescOf(r, &di) && di.Width != d0.Width) {
        ++warned;
        Log("overlay size mismatch: page %ux%u bound with %s %ux%u fmt %d bind 0x%x misc 0x%x",
            d0.Width, d0.Height, i == n ? "depth" : "target", di.Width, di.Height,
            (int)di.Format, di.BindFlags, di.MiscFlags);
      }
      if (r) r->Release();
    }
  }
  if (sc != g_rtScale[S]) {
    g_rtScale[S] = sc;
    ApplyViewports<S>(c);
    if (sc > 1.0f && g_vpScaledLogged++ < 3) Log("overlay RT bound: viewports scaled x%.0f", sc);
  }
}

template <int S>
void STDMETHODCALLTYPE HookRSSetVP(ID3D11DeviceContext* c, UINT n, const D3D11_VIEWPORT* v) {
  g_lastVPn[S] = n > 4 ? 4 : n;
  for (UINT i = 0; i < g_lastVPn[S]; ++i) g_lastVP[S][i] = v[i];
  if (g_rtScale[S] == 1.0f) {
    g_origRSSetVP[S](c, n, v);
    return;
  }
  D3D11_VIEWPORT t[4];
  for (UINT i = 0; i < g_lastVPn[S]; ++i) {
    t[i] = v[i];
    t[i].TopLeftX *= g_rtScale[S];
    t[i].TopLeftY *= g_rtScale[S];
    t[i].Width *= g_rtScale[S];
    t[i].Height *= g_rtScale[S];
  }
  g_origRSSetVP[S](c, g_lastVPn[S], t);
}

template <int S>
void STDMETHODCALLTYPE HookRSSetSci(ID3D11DeviceContext* c, UINT n, const D3D11_RECT* r) {
  g_lastScin[S] = n > 4 ? 4 : n;
  for (UINT i = 0; i < g_lastScin[S]; ++i) g_lastSci[S][i] = r[i];
  if (g_rtScale[S] == 1.0f) {
    g_origRSSetSci[S](c, n, r);
    return;
  }
  D3D11_RECT t[4];
  for (UINT i = 0; i < g_lastScin[S]; ++i) {
    t[i].left = (LONG)(r[i].left * g_rtScale[S]);
    t[i].top = (LONG)(r[i].top * g_rtScale[S]);
    t[i].right = (LONG)(r[i].right * g_rtScale[S]);
    t[i].bottom = (LONG)(r[i].bottom * g_rtScale[S]);
  }
  g_origRSSetSci[S](c, g_lastScin[S], t);
}

// ---- overlay diagnostics (DumpTextures=3): how are the overlay pages used?
constexpr int kVtCopySubRegion = 46, kVtCopyResource = 47;
using CopySubRegionFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11Resource*, UINT,
                                                  UINT, UINT, UINT, ID3D11Resource*, UINT,
                                                  const D3D11_BOX*);
using CopyResourceFn = void(STDMETHODCALLTYPE*)(ID3D11DeviceContext*, ID3D11Resource*,
                                                 ID3D11Resource*);
CopySubRegionFn g_origCopySub[2] = {};
CopyResourceFn g_origCopyRes[2] = {};
OMSetRTFn g_origOMSetRTDiag[2] = {};
int g_ovDiag = 0;

bool DescOf(ID3D11Resource* r, D3D11_TEXTURE2D_DESC* d) {
  if (!r) return false;
  D3D11_RESOURCE_DIMENSION dim;
  r->GetType(&dim);
  if (dim != D3D11_RESOURCE_DIMENSION_TEXTURE2D) return false;
  static_cast<ID3D11Texture2D*>(r)->GetDesc(d);
  return true;
}
bool IsOverlayRes(ID3D11Resource* r) {
  D3D11_TEXTURE2D_DESC d;
  return DescOf(r, &d) && IsOverlayRT(&d);
}
void LogRes(const char* what, ID3D11Resource* r) {
  D3D11_TEXTURE2D_DESC d = {};
  if (DescOf(r, &d))
    Log("  %s %ux%u fmt %d mips %u bind 0x%x misc 0x%x usage %d", what, d.Width, d.Height,
        (int)d.Format, d.MipLevels, d.BindFlags, d.MiscFlags, (int)d.Usage);
  else
    Log("  %s (not a 2D texture)", what);
}

template <int S>
void STDMETHODCALLTYPE DiagCopySub(ID3D11DeviceContext* c, ID3D11Resource* dst, UINT ds, UINT x,
                                   UINT y, UINT z, ID3D11Resource* src, UINT ss,
                                   const D3D11_BOX* box) {
  if (g_ovDiag < 400 && (IsOverlayRes(dst) || IsOverlayRes(src))) {
    ++g_ovDiag;
    Log("ovl CopySubresourceRegion ctx%d dst sub %u at %u,%u src sub %u box %s", S, ds, x, y, ss,
        box ? "yes" : "full");
    LogRes("dst", dst);
    LogRes("src", src);
  }
  g_origCopySub[S](c, dst, ds, x, y, z, src, ss, box);
}
template <int S>
void STDMETHODCALLTYPE DiagCopyRes(ID3D11DeviceContext* c, ID3D11Resource* dst,
                                   ID3D11Resource* src) {
  if (g_ovDiag < 400 && (IsOverlayRes(dst) || IsOverlayRes(src))) {
    ++g_ovDiag;
    Log("ovl CopyResource ctx%d", S);
    LogRes("dst", dst);
    LogRes("src", src);
  }
  g_origCopyRes[S](c, dst, src);
}
template <int S>
void STDMETHODCALLTYPE DiagOMSetRT(ID3D11DeviceContext* c, UINT n,
                                   ID3D11RenderTargetView* const* rtv,
                                   ID3D11DepthStencilView* dsv) {
  if (g_ovDiag < 400 && n && rtv && rtv[0]) {
    ID3D11Resource* r = nullptr;
    rtv[0]->GetResource(&r);
    if (r && IsOverlayRes(r)) {
      static std::unordered_set<uint64_t> seen;
      D3D11_TEXTURE2D_DESC rd, dd = {};
      DescOf(r, &rd);
      ID3D11Resource* dr = nullptr;
      if (dsv) dsv->GetResource(&dr);
      if (dr) DescOf(dr, &dd);
      D3D11_RENDER_TARGET_VIEW_DESC vd;
      rtv[0]->GetDesc(&vd);
      uint64_t key = ((uint64_t)rd.Width << 40) | ((uint64_t)dd.Width << 20) |
                     ((uint64_t)vd.Texture2D.MipSlice << 8) | n;
      if (seen.insert(key).second) {
        ++g_ovDiag;
        Log("ovl RT bound ctx%d: %ux%u mip %u (%u RTVs) dsv %s %ux%u fmt %d", S, rd.Width,
            rd.Height, vd.Texture2D.MipSlice, n, dsv ? "yes" : "none", dd.Width, dd.Height,
            (int)dd.Format);
      }
      if (dr) dr->Release();
    }
    if (r) r->Release();
  }
  g_origOMSetRTDiag[S](c, n, rtv, dsv);
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
  if (g_cfg.overlayScale) {
    PatchVt(vt, kVtOMSetRT, slot == 0 ? (void*)&HookOMSetRT<0> : (void*)&HookOMSetRT<1>,
            reinterpret_cast<void**>(&g_origOMSetRT[slot]));
    PatchVt(vt, kVtRSSetViewports, slot == 0 ? (void*)&HookRSSetVP<0> : (void*)&HookRSSetVP<1>,
            reinterpret_cast<void**>(&g_origRSSetVP[slot]));
    PatchVt(vt, kVtRSSetScissor, slot == 0 ? (void*)&HookRSSetSci<0> : (void*)&HookRSSetSci<1>,
            reinterpret_cast<void**>(&g_origRSSetSci[slot]));
  }
  if (g_cfg.overlayDiag) {
    PatchVt(vt, kVtCopySubRegion, slot == 0 ? (void*)&DiagCopySub<0> : (void*)&DiagCopySub<1>,
            reinterpret_cast<void**>(&g_origCopySub[slot]));
    PatchVt(vt, kVtCopyResource, slot == 0 ? (void*)&DiagCopyRes<0> : (void*)&DiagCopyRes<1>,
            reinterpret_cast<void**>(&g_origCopyRes[slot]));
    if (!g_cfg.overlayScale)
      PatchVt(vt, kVtOMSetRT, slot == 0 ? (void*)&DiagOMSetRT<0> : (void*)&DiagOMSetRT<1>,
              reinterpret_cast<void**>(&g_origOMSetRTDiag[slot]));
  }
  if (g_cfg.dumpTextures) {
    PatchVt(vt, kVtPSSetSRV, slot == 0 ? (void*)&HookPSSetSRV<0> : (void*)&HookPSSetSRV<1>,
            reinterpret_cast<void**>(&g_origPSSetSRV[slot]));
    PatchVt(vt, kVtDrawIndexed,
            slot == 0 ? (void*)&HookDrawIndexed<0> : (void*)&HookDrawIndexed<1>,
            reinterpret_cast<void**>(&g_origDrawIndexed[slot]));
    PatchVt(vt, kVtDraw, slot == 0 ? (void*)&HookDraw<0> : (void*)&HookDraw<1>,
            reinterpret_cast<void**>(&g_origDraw[slot]));
  }
}

bool g_vtHooked = false;

// DXVK shares one method table per class, so hooking the tables of a throwaway
// device covers the game's device and contexts too.
// Installs the method-table hooks from a device and its immediate context. DXVK (and
// d3d11.dll) share one table per class, so this covers every device / context.
bool g_d3dOk = false;  // method-table hooks are in place

void HookFromDevice(ID3D11Device* dev, ID3D11DeviceContext* imm, const char* how) {
  if (g_vtHooked || !dev) return;
  g_vtHooked = true;
  g_d3dOk = true;
  PatchVt(*reinterpret_cast<void***>(dev), kVtCreatePS, (void*)&HookCreatePS,
          reinterpret_cast<void**>(&g_origCreatePS));
  float s = ConfiguredScale();
  if (g_cfg.aiArt && s >= 1.25f) {
    if (s > 2.5f) LoadArtManifest(L"x3");
    if (g_art.empty()) LoadArtManifest(L"x2");  // releases ship 2x only
  }
  if (!g_art.empty() || g_cfg.dumpTextures || g_cfg.overlayScale)
    PatchVt(*reinterpret_cast<void***>(dev), kVtCreateTex2D, (void*)&HookCreateTex2D,
            reinterpret_cast<void**>(&g_origCreateTex2D));
  if (imm) HookContextVt(imm);
  ID3D11DeviceContext* def = nullptr;
  if (SUCCEEDED(dev->CreateDeferredContext(0, &def)) && def) {
    HookContextVt(def);
    def->Release();
  }
  Log("D3D11 hooks installed via %s (CreatePixelShader, PSSetShader x%d)", how,
      g_ctxVt[1] ? 2 : 1);
}

// Primary path: the game's own D3D11CreateDevice (import table of the exe).
using CreateDeviceFn = HRESULT(WINAPI*)(IDXGIAdapter*, D3D_DRIVER_TYPE, HMODULE, UINT,
                                        const D3D_FEATURE_LEVEL*, UINT, UINT, ID3D11Device**,
                                        D3D_FEATURE_LEVEL*, ID3D11DeviceContext**);
CreateDeviceFn g_origCreateDevice = nullptr;

HRESULT WINAPI HookD3D11CreateDevice(IDXGIAdapter* ad, D3D_DRIVER_TYPE dt, HMODULE sw, UINT fl,
                                     const D3D_FEATURE_LEVEL* lv, UINT nlv, UINT sdk,
                                     ID3D11Device** dev, D3D_FEATURE_LEVEL* got,
                                     ID3D11DeviceContext** imm) {
  HRESULT hr = g_origCreateDevice(ad, dt, sw, fl, lv, nlv, sdk, dev, got, imm);
  if (SUCCEEDED(hr) && dev && *dev && IsActive()) {
    ID3D11DeviceContext* ctx = imm ? *imm : nullptr;
    if (!ctx) (*dev)->GetImmediateContext(&ctx);
    HookFromDevice(*dev, ctx, "the game's device");
    if (!imm && ctx) ctx->Release();
  }
  return hr;
}

// A throwaway helper device: DXVK / d3d11.dll share one method table per class, so its
// tables are the game's. Creating it can fail transiently (e.g. under gamescope with
// little free VRAM), so it is retried; the caller decides when to give up.
int g_helperFailures = 0;
void HookD3DVtables(int attempts) {
  if (g_vtHooked) return;
  if (g_cfg.testSafeMode) {
    if (g_helperFailures++ == 0) Log("TestSafeMode: simulating a hook failure");
    return;
  }
  HMODULE d3d = GetModuleHandleA("d3d11.dll");
  if (!d3d) d3d = LoadLibraryA("d3d11.dll");
  auto create = reinterpret_cast<CreateDeviceFn>(
      d3d ? GetProcAddress(d3d, "D3D11CreateDevice") : nullptr);
  if (!create) {
    if (g_helperFailures++ == 0) Log("d3d11 not available");
    return;
  }
  ID3D11Device* dev = nullptr;
  ID3D11DeviceContext* imm = nullptr;
  HRESULT hr = E_FAIL;
  for (int attempt = 0; attempt < attempts && FAILED(hr); ++attempt) {
    if (attempt) Sleep(150);
    hr = create(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION,
                &dev, nullptr, &imm);
  }
  if (FAILED(hr) || !dev) {
    if (g_helperFailures++ < 5)
      Log("helper D3D11 device failed (0x%08lx), will retry", (unsigned long)hr);
    return;
  }
  HookFromDevice(dev, imm, "a helper device");
  if (imm) imm->Release();
  dev->Release();
}

HWND WINAPI HookCreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR name, DWORD style, int x, int y,
                                int w, int h, HWND parent, HMENU menu, HINSTANCE inst,
                                LPVOID param) {
  // before the game creates its device, shaders and textures: everything is hooked
  if (IsActive()) HookD3DVtables(8);
  return g_origCreateWindowExA(ex, cls, name, style, x, y, w, h, parent, menu, inst, param);
}
}  // namespace

void InstallD3DHooks() {
  InitializeCriticalSection(&g_cs);
  g_origCreateWindowExA = reinterpret_cast<CreateWindowExAFn>(PatchIAT(
      GetModuleHandleW(nullptr), "USER32.dll", "CreateWindowExA", (void*)&HookCreateWindowExA));
  Log("CreateWindowExA hook %s", g_origCreateWindowExA ? "ok" : "FAILED");
  g_origCreateDevice = reinterpret_cast<CreateDeviceFn>(PatchIAT(
      GetModuleHandleW(nullptr), "d3d11.dll", "D3D11CreateDevice", (void*)&HookD3D11CreateDevice));
  Log("D3D11CreateDevice hook %s", g_origCreateDevice ? "ok" : "FAILED (helper device fallback)");
}

// Later chances (first frames); bounded so a broken setup does not retry forever.
void HookD3DVtablesOnce() {
  if (IsActive() && !g_vtHooked && g_helperFailures < 40) HookD3DVtables(1);
}

// Last attempt before the UI is laid out (core decides on safe mode right after).
void EnsureD3DHooks() {
  if (IsActive() && !g_vtHooked) HookD3DVtables(8);
}

bool D3DHooksReady() { return g_d3dOk; }
}  // namespace uis
