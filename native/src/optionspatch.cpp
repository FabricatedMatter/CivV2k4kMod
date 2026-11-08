// Patches whichever options menu the game loads (base game, EUI, ...) at startup so the
// UI Scale controls appear in it, independent of the install order of UI mods.
// The engine resolves <LuaContext FileName="Assets/UI/Options/OptionsMenu"> by file name
// and DLC copies win, so a UI mod's OptionsMenu.{lua,xml} replaces the base one. Instead of
// shipping a modified copy of one of them, every copy found under Assets\ is patched into
// a cache and served through the file redirect under its original path.
//   XML: a hidden UIScaleBlock is inserted after the PinchSpeedSlider element (present in
//        the base menu and in EUI's).
//   Lua: ContextPtr:SetShowHideHandler(f) becomes UIScale_SetShowHide(ContextPtr, f), which
//        also calls UIScaleOnShowHide; the file then includes UIScaleOptions.lua
//        (mod/Override/Assets/UI/Options), which holds the actual logic.
#include "optionspatch.h"

#include <cctype>
#include <cstring>

#ifdef _WIN32
#include <windows.h>
#include <shlwapi.h>

#include "log.h"
#endif

namespace uis {
namespace {

const char* const kAnchorId = "PinchSpeedSlider";
const char* const kParentName = "UIScaleParent";  // ID given to an unnamed parent

// The block inserted into the XML; the leading number is the nesting depth.
const struct {
  int depth;
  const char* text;
} kBlock[] = {
    {0, "<!-- UI Scale (Civ V UI Scale mod, added by version.dll) -->"},
    {0, "<Stack ID=\"UIScaleBlock\" Anchor=\"C,C\" Padding=\"4\" Hidden=\"1\" >"},
    // Both rows stay within the 300 px of the other sliders (wider rows run into the
    // neighbouring column). Row 1: value + Automatic; row 2: slider + Apply.
    {1, "<Stack ID=\"UIScaleRow\" Anchor=\"C,C\" StackGrowth=\"Right\" Padding=\"8\" >"},
    {2, "<Label Anchor=\"L,C\" ID=\"UIScaleValue\" String=\"UI Scale: 100%\" "
        "ToolTip=\"Size of all interface elements, independent of the screen resolution.\" />"},
    // A CheckBox only occupies its box in a stack and draws its text to the left of it,
    // over the previous item: reserve room for "Automatic".
    {2, "<Container Size=\"84,1\" />"},
    {2, "<CheckBox Anchor=\"L,C\" String=\"Automatic\" IsChecked=\"1\" ID=\"UIScaleAutoCheck\" "
        "ToolTip=\"Pick the scale from the screen height (1080p = 100%).\" />"},
    {1, "</Stack>"},
    {1, "<Stack ID=\"UIScaleRow2\" Anchor=\"C,C\" StackGrowth=\"Right\" Padding=\"8\" >"},
    {2, "<Slider Anchor=\"L,C\" Length=\"220\" Style=\"Slider\" ID=\"UIScaleSlider\" "
        "WheelSteps=\"50\" "
        "ToolTip=\"Size of all interface elements, independent of the screen resolution.\" />"},
    {2, "<GridButton Anchor=\"L,C\" Size=\"72,26\" ID=\"UIScaleApplyButton\" "
        "Style=\"SmallButton\" "
        "ToolTip=\"Apply the UI scale now. It is also applied when you press Accept.\" >"},
    {3, "<Label Anchor=\"C,C\" String=\"Apply\" Font=\"TwCenMT18\" "
        "ColorSet=\"Beige_Black_Alpha\" FontStyle=\"Shadow\" />"},
    {2, "</GridButton>"},
    {1, "</Stack>"},
    {1, "<Label Anchor=\"C,C\" ID=\"UIScaleWarning\" WrapWidth=\"420\" Hidden=\"1\" "
        "String=\"\" />"},
    {0, "</Stack>"},
};

// Defined before the file's own code, on its first line (keeps Lua line numbers intact).
// A global, not a local: big menus come close to Lua 5.1's 200-locals limit.
const char* const kLuaPrologue =
    "UIScale_SetShowHide = function(ctx, f) ctx:SetShowHideHandler(function(h, i) "
    "if f then f(h, i) end if UIScaleOnShowHide then UIScaleOnShowHide(h, i) end end) end; ";

bool IsWs(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
bool IsIdent(char c) { return isalnum((unsigned char)c) || c == '_'; }

bool IEquals(const std::string& a, const char* b) {
  size_t n = strlen(b);
  if (a.size() != n) return false;
  for (size_t i = 0; i < n; ++i)
    if (tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return false;
  return true;
}

std::string Eol(const std::string& s) { return s.find("\r\n") != std::string::npos ? "\r\n" : "\n"; }

// Value of attribute `key` in a start tag's text (between the name and '>').
std::string Attr(const std::string& s, size_t from, size_t to, const char* key) {
  size_t p = from;
  while (p < to) {
    while (p < to && (IsWs(s[p]) || s[p] == '/')) ++p;
    size_t n0 = p;
    while (p < to && !IsWs(s[p]) && s[p] != '=' && s[p] != '/') ++p;
    std::string name = s.substr(n0, p - n0);
    while (p < to && IsWs(s[p])) ++p;
    if (p >= to || s[p] != '=') continue;
    ++p;
    while (p < to && IsWs(s[p])) ++p;
    if (p >= to) break;
    char q = s[p];
    if (q != '"' && q != '\'') continue;
    size_t v0 = ++p;
    while (p < to && s[p] != q) ++p;
    std::string val = s.substr(v0, p - v0);
    ++p;
    if (IEquals(name, key)) return val;
  }
  return "";
}

struct Elem {
  std::string name, id;
  size_t nameEnd;  // offset just after the tag name in the start tag
};
}  // namespace

bool PatchOptionsXml(const std::string& s, std::string& out, std::string& parentId,
                     std::string& why) {
  if (s.find("\"UIScaleBlock\"") != std::string::npos) {
    why = "already contains a UIScaleBlock";
    return false;
  }
  std::vector<Elem> stack;
  Elem parent;
  size_t anchorStart = std::string::npos, insertAt = std::string::npos, anchorDepth = 0;
  size_t p = 0;
  while (insertAt == std::string::npos && (p = s.find('<', p)) != std::string::npos) {
    const struct {
      const char *open, *close;
    } skips[] = {{"<!--", "-->"}, {"<![CDATA[", "]]>"}, {"<?", "?>"}, {"<!", ">"}};
    bool skipped = false;
    for (auto& k : skips) {
      if (s.compare(p, strlen(k.open), k.open) != 0) continue;
      size_t e = s.find(k.close, p + strlen(k.open));
      if (e == std::string::npos) {
        why = "unterminated comment or declaration";
        return false;
      }
      p = e + strlen(k.close);
      skipped = true;
      break;
    }
    if (skipped) continue;
    size_t q = p + 1;  // tag end ('>' outside quotes)
    for (char quote = 0; q < s.size(); ++q) {
      char c = s[q];
      if (quote) {
        if (c == quote) quote = 0;
      } else if (c == '"' || c == '\'') {
        quote = c;
      } else if (c == '>') {
        break;
      }
    }
    if (q >= s.size()) {
      why = "unterminated tag";
      return false;
    }
    if (s[p + 1] == '/') {  // end tag: pop up to the matching element
      size_t n0 = p + 2, n1 = n0;
      while (n1 < q && !IsWs(s[n1])) ++n1;
      std::string name = s.substr(n0, n1 - n0);
      while (!stack.empty()) {
        Elem e = stack.back();
        stack.pop_back();
        if (e.name == name) {
          if (anchorStart != std::string::npos && stack.size() == anchorDepth) insertAt = q + 1;
          break;
        }
      }
      p = q + 1;
      continue;
    }
    bool selfClose = s[q - 1] == '/';
    size_t n1 = p + 1;
    while (n1 < q && !IsWs(s[n1]) && s[n1] != '/') ++n1;
    Elem el{s.substr(p + 1, n1 - p - 1), Attr(s, n1, q, "ID"), n1};
    if (anchorStart == std::string::npos && el.id == kAnchorId) {
      if (stack.empty()) {
        why = "anchor has no parent element";
        return false;
      }
      parent = stack.back();
      anchorStart = p;
      anchorDepth = stack.size();
      if (selfClose) insertAt = q + 1;
    }
    if (!selfClose) stack.push_back(el);
    p = q + 1;
  }
  if (anchorStart == std::string::npos) {
    why = std::string("anchor ID=\"") + kAnchorId + "\" not found";
    return false;
  }
  if (insertAt == std::string::npos) {
    why = "anchor element is not closed";
    return false;
  }

  // Indent like the anchor's line.
  size_t ls = s.rfind('\n', anchorStart);
  ls = ls == std::string::npos ? 0 : ls + 1;
  size_t le = ls;
  while (le < anchorStart && (s[le] == ' ' || s[le] == '\t')) ++le;
  std::string indent = s.substr(ls, le - ls);
  std::string unit = indent.empty() || indent.find('\t') != std::string::npos ? "\t" : "  ";
  std::string eol = Eol(s), block;
  for (auto& l : kBlock) {
    block += eol + indent;
    for (int i = 0; i < l.depth; ++i) block += unit;
    block += l.text;
  }

  out = s;
  out.insert(insertAt, block);
  if (parent.id.empty()) {  // name it so the Lua side can re-layout it
    out.insert(parent.nameEnd, std::string(" ID=\"") + kParentName + "\"");
    parentId = kParentName;
  } else {
    parentId = parent.id;
  }
  for (char c : parentId)
    if (!IsIdent(c)) {
      why = "parent ID \"" + parentId + "\" is not a plain name";
      return false;
    }
  return true;
}

bool PatchOptionsLua(const std::string& s, const std::string& parentId, std::string& out,
                     int& hooks, std::string& why) {
  if (s.find("UIScaleOptions") != std::string::npos) {
    why = "already includes UIScaleOptions";
    return false;
  }
  hooks = 0;
  size_t bom = s.compare(0, 3, "\xEF\xBB\xBF") == 0 ? 3 : 0;
  out = s.substr(0, bom) + kLuaPrologue;
  const std::string ctx = "ContextPtr", fn = "SetShowHideHandler";
  size_t i = bom, p;
  while ((p = s.find(ctx, i)) != std::string::npos) {
    size_t q = p + ctx.size();
    auto ws = [&] {
      while (q < s.size() && IsWs(s[q])) ++q;
    };
    bool boundary = (p == 0 || (!IsIdent(s[p - 1]) && s[p - 1] != '.' && s[p - 1] != ':'));
    bool match = false;
    if (boundary) {
      ws();
      if (q < s.size() && s[q] == ':') {
        ++q;
        ws();
        if (s.compare(q, fn.size(), fn) == 0) {
          q += fn.size();
          ws();
          match = q < s.size() && s[q] == '(';
          ++q;
        }
      }
    }
    if (!match) {
      out.append(s, i, p + ctx.size() - i);
      i = p + ctx.size();
      continue;
    }
    out.append(s, i, p - i);
    out += "UIScale_SetShowHide(ContextPtr";
    size_t r = q;
    while (r < s.size() && IsWs(s[r])) ++r;
    if (r < s.size() && s[r] != ')') out += ", ";
    for (size_t k = p; k < q; ++k)  // keep line breaks inside the replaced text
      if (s[k] == '\n') out += '\n';
    i = q;
    ++hooks;
  }
  out.append(s, i, std::string::npos);
  std::string eol = Eol(s);
  out += eol + "-- Civ V UI Scale (added by version.dll)" + eol;
  out += "UIScaleParentID = \"" + parentId + "\";" + eol;
  out += "include(\"UIScaleOptions\");" + eol;
  out += "if UIScale ~= nil and UIScale.ShotConfig ~= nil and UIScale.ShotConfig() then "
         "include(\"UIScaleShots\"); end" + eol;
  (void)why;
  return true;
}

#ifdef _WIN32
namespace {
bool ReadAll(const std::wstring& path, std::string& data) {
  HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0,
                         nullptr);
  if (h == INVALID_HANDLE_VALUE) return false;
  DWORD size = GetFileSize(h, nullptr), got = 0;
  data.resize(size);
  BOOL ok = size == INVALID_FILE_SIZE ? FALSE
                                      : (size == 0 || ReadFile(h, &data[0], size, &got, nullptr));
  CloseHandle(h);
  return ok && got == size;
}

bool WriteIfChanged(const std::wstring& path, const std::string& data) {
  std::string old;
  if (ReadAll(path, old) && old == data) return true;
  for (size_t p = path.find(L'\\', 3); p != std::wstring::npos; p = path.find(L'\\', p + 1))
    CreateDirectoryW(path.substr(0, p).c_str(), nullptr);
  HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr);
  if (h == INVALID_HANDLE_VALUE) return false;
  DWORD put = 0;
  BOOL ok = data.empty() || WriteFile(h, data.data(), (DWORD)data.size(), &put, nullptr);
  CloseHandle(h);
  return ok && put == data.size();
}

struct Found {
  std::wstring rel;  // directory relative to the game dir, original case
  std::wstring lua, xml;  // file names as found (empty if missing)
};

void FindMenus(const std::wstring& dir, const std::wstring& rel, std::vector<Found>& out) {
  WIN32_FIND_DATAW fd;
  HANDLE h = FindFirstFileW((dir + L"\\*").c_str(), &fd);
  if (h == INVALID_HANDLE_VALUE) return;
  Found f{rel, L"", L""};
  std::vector<std::wstring> subdirs;
  do {
    std::wstring name = fd.cFileName;
    if (name == L"." || name == L"..") continue;
    if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
      subdirs.push_back(name);
    else if (_wcsicmp(name.c_str(), L"OptionsMenu.lua") == 0)
      f.lua = name;
    else if (_wcsicmp(name.c_str(), L"OptionsMenu.xml") == 0)
      f.xml = name;
  } while (FindNextFileW(h, &fd));
  FindClose(h);
  if (!f.lua.empty() || !f.xml.empty()) out.push_back(f);
  for (auto& d : subdirs) FindMenus(dir + L"\\" + d, rel + L"\\" + d, out);
}
}  // namespace

std::vector<std::pair<std::wstring, std::wstring>> PatchOptionsMenus(const std::wstring& gameDir,
                                                                     const std::wstring& cacheDir,
                                                                     bool thirdParty) {
  std::vector<std::pair<std::wstring, std::wstring>> result;
  DWORD t0 = GetTickCount();
  std::vector<Found> menus;
  FindMenus(gameDir + L"\\Assets", L"Assets", menus);
  for (auto& m : menus) {
    bool base = _wcsicmp(m.rel.c_str(), L"Assets\\UI\\Options") == 0;
    const char* tag = base ? " (base game)"
                      : StrStrIW(m.rel.c_str(), L"\\UI_bc1\\") ? " (EUI)"
                                                                : "";
    if (!base && !thirdParty) {
      Log("options menu skipped: %ls%s (PatchOptionsMenu=0)", m.rel.c_str(), tag);
      continue;
    }
    if (m.lua.empty() || m.xml.empty()) {
      Log("options menu skipped: %ls%s has only %ls", m.rel.c_str(), tag,
          m.lua.empty() ? m.xml.c_str() : m.lua.c_str());
      continue;
    }
    std::wstring src = gameDir + L"\\" + m.rel + L"\\";
    std::string lua, xml, luaOut, xmlOut, parentId, why;
    int hooks = 0;
    if (!ReadAll(src + m.lua, lua) || !ReadAll(src + m.xml, xml)) {
      Log("options menu skipped: %ls%s (cannot read)", m.rel.c_str(), tag);
      continue;
    }
    if (!PatchOptionsXml(xml, xmlOut, parentId, why) ||
        !PatchOptionsLua(lua, parentId, luaOut, hooks, why)) {
      Log("options menu skipped: %ls%s: %s", m.rel.c_str(), tag, why.c_str());
      continue;
    }
    std::wstring dst = cacheDir + L"\\" + m.rel + L"\\";
    if (!WriteIfChanged(dst + m.lua, luaOut) || !WriteIfChanged(dst + m.xml, xmlOut)) {
      Log("options menu skipped: %ls%s (cannot write %ls, error %lu)", m.rel.c_str(), tag,
          dst.c_str(), GetLastError());
      continue;
    }
    result.push_back({m.rel + L"\\" + m.lua, dst + m.lua});
    result.push_back({m.rel + L"\\" + m.xml, dst + m.xml});
    Log("options menu patched: %ls%s (parent %s, %d show/hide handler%s hooked)", m.rel.c_str(),
        tag, parentId.c_str(), hooks, hooks == 1 ? "" : "s");
    if (hooks == 0) Log("  no SetShowHideHandler found: the slider is only refreshed on load");
  }
  Log("options menus: %u found, %u patched (%lu ms)", (unsigned)menus.size(),
      (unsigned)result.size() / 2, GetTickCount() - t0);
  return result;
}
#endif
}  // namespace uis
