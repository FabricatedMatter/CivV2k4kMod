// Virtual file overlay: files under the override roots replace or extend the
// game directory as seen by the exe. Nothing in the game folder is modified.
//  - CreateFile*/GetFileAttributes*: paths that have an override are redirected
//  - FindFirstFile*/FindNextFile*: override files that do not exist in the game
//    folder are appended to directory listings, so the game's asset index finds
//    them (used for the hi-res font atlases with new names)
#include "redirect.h"

#include <windows.h>
#include <shlwapi.h>

#include <unordered_map>
#include <vector>

#include "hook.h"
#include "log.h"

namespace uis {
namespace {
std::wstring g_gameDirLower;  // with trailing backslash
std::unordered_map<std::wstring, std::wstring> g_map;  // lower relative path -> full override path

// Override-only files per directory (lower relative dir -> entries).
struct Extra {
  std::wstring name;      // original-case file name
  std::wstring fullPath;  // override file
};
std::unordered_map<std::wstring, std::vector<Extra>> g_extras;

using CreateFileWFn = HANDLE(WINAPI*)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD,
                                      HANDLE);
using CreateFileAFn = HANDLE(WINAPI*)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD,
                                      HANDLE);
using GetAttrExWFn = BOOL(WINAPI*)(LPCWSTR, GET_FILEEX_INFO_LEVELS, LPVOID);
using GetAttrExAFn = BOOL(WINAPI*)(LPCSTR, GET_FILEEX_INFO_LEVELS, LPVOID);
using GetAttrWFn = DWORD(WINAPI*)(LPCWSTR);
using GetAttrAFn = DWORD(WINAPI*)(LPCSTR);
using FindFirstWFn = HANDLE(WINAPI*)(LPCWSTR, LPWIN32_FIND_DATAW);
using FindFirstExWFn = HANDLE(WINAPI*)(LPCWSTR, FINDEX_INFO_LEVELS, LPVOID, FINDEX_SEARCH_OPS,
                                       LPVOID, DWORD);
using FindFirstAFn = HANDLE(WINAPI*)(LPCSTR, LPWIN32_FIND_DATAA);
using FindFirstExAFn = HANDLE(WINAPI*)(LPCSTR, FINDEX_INFO_LEVELS, LPVOID, FINDEX_SEARCH_OPS,
                                       LPVOID, DWORD);
using FindNextWFn = BOOL(WINAPI*)(HANDLE, LPWIN32_FIND_DATAW);
using FindNextAFn = BOOL(WINAPI*)(HANDLE, LPWIN32_FIND_DATAA);
using FindCloseFn = BOOL(WINAPI*)(HANDLE);

CreateFileWFn g_origCreateFileW = nullptr;
CreateFileAFn g_origCreateFileA = nullptr;
GetAttrExWFn g_origGetAttrExW = nullptr;
GetAttrExAFn g_origGetAttrExA = nullptr;
GetAttrWFn g_origGetAttrW = nullptr;
GetAttrAFn g_origGetAttrA = nullptr;
FindFirstWFn g_origFindFirstW = nullptr;
FindFirstExWFn g_origFindFirstExW = nullptr;
FindFirstAFn g_origFindFirstA = nullptr;
FindFirstExAFn g_origFindFirstExA = nullptr;
FindNextWFn g_origFindNextW = nullptr;
FindNextAFn g_origFindNextA = nullptr;
FindCloseFn g_origFindClose = nullptr;

std::wstring Lower(std::wstring s) {
  for (auto& c : s) c = (wchar_t)towlower(c);
  for (auto& c : s)
    if (c == L'/') c = L'\\';
  return s;
}

std::wstring Widen(LPCSTR s) {
  wchar_t w[MAX_PATH * 2];
  if (!s || !MultiByteToWideChar(CP_ACP, 0, s, -1, w, MAX_PATH * 2)) return L"";
  return w;
}

void Scan(const std::wstring& dir, const std::wstring& rel) {
  WIN32_FIND_DATAW fd;
  HANDLE h = FindFirstFileW((dir + L"\\*").c_str(), &fd);
  if (h == INVALID_HANDLE_VALUE) return;
  do {
    std::wstring name = fd.cFileName;
    if (name == L"." || name == L"..") continue;
    std::wstring childRel = rel.empty() ? name : rel + L"\\" + name;
    if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
      Scan(dir + L"\\" + name, childRel);
    else
      g_map[Lower(childRel)] = dir + L"\\" + name;
  } while (FindNextFileW(h, &fd));
  FindClose(h);
}

// Lower-case path relative to the game dir, or empty if outside it.
std::wstring GameRel(const wchar_t* path) {
  if (!path) return L"";
  wchar_t full[MAX_PATH * 2];
  DWORD n = GetFullPathNameW(path, MAX_PATH * 2, full, nullptr);
  if (!n || n >= MAX_PATH * 2) return L"";
  std::wstring f = Lower(full);
  if (f.compare(0, g_gameDirLower.size(), g_gameDirLower) != 0) return L"";
  return f.substr(g_gameDirLower.size());
}

const std::wstring* Lookup(const wchar_t* path) {
  if (!path || g_map.empty()) return nullptr;
  std::wstring rel = GameRel(path);
  if (rel.empty()) return nullptr;
  auto it = g_map.find(rel);
  return it == g_map.end() ? nullptr : &it->second;
}

bool IsRead(DWORD access, DWORD disp) {
  return !(access & (GENERIC_WRITE | FILE_WRITE_DATA | FILE_APPEND_DATA)) &&
         (disp == OPEN_EXISTING || disp == OPEN_ALWAYS);
}

HANDLE WINAPI HookCreateFileW(LPCWSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
                              DWORD disp, DWORD flags, HANDLE tmpl) {
  if (IsRead(access, disp))
    if (auto o = Lookup(name)) return g_origCreateFileW(o->c_str(), access, share, sa, disp, flags, tmpl);
  return g_origCreateFileW(name, access, share, sa, disp, flags, tmpl);
}

HANDLE WINAPI HookCreateFileA(LPCSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
                              DWORD disp, DWORD flags, HANDLE tmpl) {
  if (name && IsRead(access, disp)) {
    std::wstring w = Widen(name);
    if (auto o = Lookup(w.c_str()))
      return CreateFileW(o->c_str(), access, share, sa, disp, flags, tmpl);
  }
  return g_origCreateFileA(name, access, share, sa, disp, flags, tmpl);
}

BOOL WINAPI HookGetAttrExW(LPCWSTR name, GET_FILEEX_INFO_LEVELS lvl, LPVOID info) {
  if (auto o = Lookup(name)) return g_origGetAttrExW(o->c_str(), lvl, info);
  return g_origGetAttrExW(name, lvl, info);
}
BOOL WINAPI HookGetAttrExA(LPCSTR name, GET_FILEEX_INFO_LEVELS lvl, LPVOID info) {
  std::wstring w = Widen(name);
  if (auto o = Lookup(w.c_str())) return GetFileAttributesExW(o->c_str(), lvl, info);
  return g_origGetAttrExA(name, lvl, info);
}
DWORD WINAPI HookGetAttrW(LPCWSTR name) {
  if (auto o = Lookup(name)) return g_origGetAttrW(o->c_str());
  return g_origGetAttrW(name);
}
DWORD WINAPI HookGetAttrA(LPCSTR name) {
  std::wstring w = Widen(name);
  if (auto o = Lookup(w.c_str())) return GetFileAttributesW(o->c_str());
  return g_origGetAttrA(name);
}

// ------------------------------------------------------------ directory listings
struct Search {
  HANDLE real;               // real find handle or INVALID_HANDLE_VALUE
  std::vector<Extra> extra;  // override-only matches still to be returned
  size_t next = 0;
};
std::unordered_map<HANDLE, Search*> g_searches;
CRITICAL_SECTION g_cs;
uintptr_t g_fakeHandle = 0x7E000000;

void FillW(const Extra& e, WIN32_FIND_DATAW* fd) {
  memset(fd, 0, sizeof(*fd));
  WIN32_FILE_ATTRIBUTE_DATA a;
  if (GetFileAttributesExW(e.fullPath.c_str(), GetFileExInfoStandard, &a)) {
    fd->dwFileAttributes = a.dwFileAttributes;
    fd->ftCreationTime = a.ftCreationTime;
    fd->ftLastAccessTime = a.ftLastAccessTime;
    fd->ftLastWriteTime = a.ftLastWriteTime;
    fd->nFileSizeHigh = a.nFileSizeHigh;
    fd->nFileSizeLow = a.nFileSizeLow;
  }
  lstrcpynW(fd->cFileName, e.name.c_str(), MAX_PATH);
}

void FillA(const Extra& e, WIN32_FIND_DATAA* fd) {
  WIN32_FIND_DATAW w;
  FillW(e, &w);
  memset(fd, 0, sizeof(*fd));
  fd->dwFileAttributes = w.dwFileAttributes;
  fd->ftCreationTime = w.ftCreationTime;
  fd->ftLastAccessTime = w.ftLastAccessTime;
  fd->ftLastWriteTime = w.ftLastWriteTime;
  fd->nFileSizeHigh = w.nFileSizeHigh;
  fd->nFileSizeLow = w.nFileSizeLow;
  WideCharToMultiByte(CP_ACP, 0, w.cFileName, -1, fd->cFileName, MAX_PATH, nullptr, nullptr);
}

// Override-only files in the directory of `pattern` that match its file spec.
std::vector<Extra> ExtrasFor(const std::wstring& pattern) {
  std::vector<Extra> out;
  if (g_extras.empty()) return out;
  size_t slash = pattern.find_last_of(L"\\/");
  if (slash == std::wstring::npos) return out;
  std::wstring dir = pattern.substr(0, slash), spec = pattern.substr(slash + 1);
  std::wstring rel = GameRel(dir.c_str());
  auto it = g_extras.find(rel);
  if (it == g_extras.end()) return out;
  for (auto& e : it->second)
    if (spec.empty() || PathMatchSpecW(e.name.c_str(), spec.c_str())) out.push_back(e);
  return out;
}

// Common FindFirst logic; `first(fdOut)` runs the real call.
template <typename FD, typename FirstFn, typename FillFn>
HANDLE FindFirstCommon(const std::wstring& pattern, FD* fd, FirstFn first, FillFn fill) {
  std::vector<Extra> extra = ExtrasFor(pattern);
  HANDLE h = first();
  if (extra.empty()) return h;
  auto* s = new Search{h, std::move(extra), 0};
  if (h == INVALID_HANDLE_VALUE) {  // no real match: serve the extras from a fake handle
    fill(s->extra[s->next++], fd);
    EnterCriticalSection(&g_cs);
    h = reinterpret_cast<HANDLE>(g_fakeHandle += 4);
    g_searches[h] = s;
    LeaveCriticalSection(&g_cs);
    SetLastError(ERROR_SUCCESS);
    return h;
  }
  EnterCriticalSection(&g_cs);
  g_searches[h] = s;
  LeaveCriticalSection(&g_cs);
  return h;
}

Search* GetSearch(HANDLE h) {
  EnterCriticalSection(&g_cs);
  auto it = g_searches.find(h);
  Search* s = it == g_searches.end() ? nullptr : it->second;
  LeaveCriticalSection(&g_cs);
  return s;
}

HANDLE WINAPI HookFindFirstW(LPCWSTR p, LPWIN32_FIND_DATAW fd) {
  return FindFirstCommon(p ? p : L"", fd, [&] { return g_origFindFirstW(p, fd); }, FillW);
}
HANDLE WINAPI HookFindFirstExW(LPCWSTR p, FINDEX_INFO_LEVELS lvl, LPVOID fd, FINDEX_SEARCH_OPS op,
                               LPVOID filter, DWORD flags) {
  return FindFirstCommon(p ? p : L"", static_cast<WIN32_FIND_DATAW*>(fd),
                         [&] { return g_origFindFirstExW(p, lvl, fd, op, filter, flags); }, FillW);
}
HANDLE WINAPI HookFindFirstA(LPCSTR p, LPWIN32_FIND_DATAA fd) {
  return FindFirstCommon(Widen(p), fd, [&] { return g_origFindFirstA(p, fd); }, FillA);
}
HANDLE WINAPI HookFindFirstExA(LPCSTR p, FINDEX_INFO_LEVELS lvl, LPVOID fd, FINDEX_SEARCH_OPS op,
                               LPVOID filter, DWORD flags) {
  return FindFirstCommon(Widen(p), static_cast<WIN32_FIND_DATAA*>(fd),
                         [&] { return g_origFindFirstExA(p, lvl, fd, op, filter, flags); }, FillA);
}

template <typename FD, typename NextFn, typename FillFn>
BOOL FindNextCommon(HANDLE h, FD* fd, NextFn next, FillFn fill) {
  Search* s = GetSearch(h);
  if (!s) return next();
  if (s->real != INVALID_HANDLE_VALUE && next()) return TRUE;
  if (s->next < s->extra.size()) {
    fill(s->extra[s->next++], fd);
    SetLastError(ERROR_SUCCESS);
    return TRUE;
  }
  SetLastError(ERROR_NO_MORE_FILES);
  return FALSE;
}

BOOL WINAPI HookFindNextW(HANDLE h, LPWIN32_FIND_DATAW fd) {
  return FindNextCommon(h, fd, [&] { return g_origFindNextW(h, fd); }, FillW);
}
BOOL WINAPI HookFindNextA(HANDLE h, LPWIN32_FIND_DATAA fd) {
  return FindNextCommon(h, fd, [&] { return g_origFindNextA(h, fd); }, FillA);
}

BOOL WINAPI HookFindClose(HANDLE h) {
  EnterCriticalSection(&g_cs);
  auto it = g_searches.find(h);
  Search* s = nullptr;
  if (it != g_searches.end()) {
    s = it->second;
    g_searches.erase(it);
  }
  LeaveCriticalSection(&g_cs);
  if (!s) return g_origFindClose(h);
  BOOL r = s->real != INVALID_HANDLE_VALUE ? g_origFindClose(s->real) : TRUE;
  delete s;
  return r;
}

template <typename T>
void Hook(HMODULE exe, const char* fn, T hook, T* orig) {
  *orig = reinterpret_cast<T>(PatchIAT(exe, "KERNEL32.dll", fn, (void*)hook));
  if (!*orig) Log("  %s import not hooked", fn);
}
}  // namespace

bool InstallFileRedirect(const std::wstring& gameDir, const std::vector<std::wstring>& roots) {
  InitializeCriticalSection(&g_cs);
  g_gameDirLower = Lower(gameDir);
  if (g_gameDirLower.empty() || g_gameDirLower.back() != L'\\') g_gameDirLower += L'\\';
  for (auto& r : roots) Scan(r, L"");

  // Files that do not exist in the game folder must be listed virtually.
  for (auto& kv : g_map) {
    std::wstring real = gameDir + L"\\" + kv.first;
    if (GetFileAttributesW(real.c_str()) != INVALID_FILE_ATTRIBUTES) continue;
    size_t slash = kv.first.find_last_of(L'\\');
    std::wstring dir = slash == std::wstring::npos ? L"" : kv.first.substr(0, slash);
    size_t s2 = kv.second.find_last_of(L'\\');
    g_extras[dir].push_back({kv.second.substr(s2 + 1), kv.second});
  }
  size_t extraCount = 0;
  for (auto& kv : g_extras) extraCount += kv.second.size();
  Log("file overrides: %u (%u new files in %u dirs)", (unsigned)g_map.size(),
      (unsigned)extraCount, (unsigned)g_extras.size());
  if (g_map.empty()) return true;

  HMODULE exe = GetModuleHandleW(nullptr);
  Hook(exe, "CreateFileW", &HookCreateFileW, &g_origCreateFileW);
  Hook(exe, "CreateFileA", &HookCreateFileA, &g_origCreateFileA);
  Hook(exe, "GetFileAttributesExW", &HookGetAttrExW, &g_origGetAttrExW);
  Hook(exe, "GetFileAttributesExA", &HookGetAttrExA, &g_origGetAttrExA);
  Hook(exe, "GetFileAttributesW", &HookGetAttrW, &g_origGetAttrW);
  Hook(exe, "GetFileAttributesA", &HookGetAttrA, &g_origGetAttrA);
  if (!g_extras.empty()) {
    Hook(exe, "FindFirstFileW", &HookFindFirstW, &g_origFindFirstW);
    Hook(exe, "FindFirstFileExW", &HookFindFirstExW, &g_origFindFirstExW);
    Hook(exe, "FindFirstFileA", &HookFindFirstA, &g_origFindFirstA);
    Hook(exe, "FindFirstFileExA", &HookFindFirstExA, &g_origFindFirstExA);
    Hook(exe, "FindNextFileW", &HookFindNextW, &g_origFindNextW);
    Hook(exe, "FindNextFileA", &HookFindNextA, &g_origFindNextA);
    Hook(exe, "FindClose", &HookFindClose, &g_origFindClose);
  }
  Log("file redirect hooks installed");
  return g_origCreateFileW || g_origCreateFileA;
}
}  // namespace uis
