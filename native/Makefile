CXX      := i686-w64-mingw32-g++
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -Wno-cast-function-type \
            -fno-exceptions -fno-rtti -DUNICODE -D_UNICODE -DWIN32_LEAN_AND_MEAN
LDFLAGS  := -shared -static -static-libgcc -static-libstdc++ -s \
            -Wl,--enable-stdcall-fixup -Wl,--kill-at
LIBS     := -lshell32 -lshlwapi

SRC := $(wildcard src/*.cpp)
OBJ := $(patsubst src/%.cpp,build/%.o,$(SRC))

build/version.dll: $(OBJ) version.def
	$(CXX) $(LDFLAGS) -o $@ $(OBJ) version.def $(LIBS)

build/%.o: src/%.cpp $(wildcard src/*.h) | build
	$(CXX) $(CXXFLAGS) -c $< -o $@

build:
	mkdir -p build

clean:
	rm -rf build

.PHONY: clean
