#pragma once

// The public Unicode NSIS plug-in ABI (NSIS 3, plug-in API 1.0).
// https://nsis.sourceforge.io/Examples/Plugin/nsis/api.h
// https://nsis.sourceforge.io/Examples/Plugin/nsis/pluginapi.h
// Only the stack layout and callback registration are needed; no SDK routines are linked.
struct stack_t {
  stack_t* next;
  wchar_t text[1];
};

enum NSPIM { NSPIM_UNLOAD = 0, NSPIM_GUIUNLOAD = 1 };
using NSISPLUGINCALLBACK = UINT_PTR (__cdecl*)(NSPIM);

struct extra_parameters {
  void* exec_flags;
  int (WINAPI* ExecuteCodeSegment)(int, HWND);
  void (WINAPI* validate_filename)(wchar_t*);
  int (WINAPI* RegisterPluginCallback)(HMODULE, NSISPLUGINCALLBACK);
};
