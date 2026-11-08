// Test harness for the options-menu patcher (native/src/optionspatch.cpp), host build:
//   g++ -std=c++17 -o build/optpatch_test test/optpatch_test.cpp
//   build/optpatch_test DIR OUTDIR    (DIR holds OptionsMenu.lua + OptionsMenu.xml)
#undef _WIN32
#include "../src/optionspatch.cpp"

#include <cstdio>
#include <fstream>
#include <sstream>

static std::string Read(const std::string& p) {
  std::ifstream f(p, std::ios::binary);
  std::stringstream ss;
  ss << f.rdbuf();
  return ss.str();
}

int main(int argc, char** argv) {
  if (argc != 3) {
    fprintf(stderr, "usage: %s DIR OUTDIR\n", argv[0]);
    return 2;
  }
  std::string dir = argv[1], out = argv[2];
  std::string xml = Read(dir + "/OptionsMenu.xml"), lua = Read(dir + "/OptionsMenu.lua");
  std::string xmlOut, luaOut, parent, why;
  int hooks = 0;
  if (!uis::PatchOptionsXml(xml, xmlOut, parent, why) ||
      !uis::PatchOptionsLua(lua, parent, luaOut, hooks, why)) {
    printf("skipped: %s\n", why.c_str());
    return 1;
  }
  std::ofstream(out + "/OptionsMenu.xml", std::ios::binary) << xmlOut;
  std::ofstream(out + "/OptionsMenu.lua", std::ios::binary) << luaOut;
  printf("patched: parent %s, %d hooks\n", parent.c_str(), hooks);
  // patching the output again must be refused (no double patch)
  std::string x2, l2, p2, w2;
  if (uis::PatchOptionsXml(xmlOut, x2, p2, w2) || uis::PatchOptionsLua(luaOut, p2, l2, hooks, w2)) {
    printf("FAIL: patched twice\n");
    return 1;
  }
  return 0;
}
