// LLDB 8+ layer: include Swift/Objective-C recognized arguments.
//
// Built against LLDB 8 headers; loaded only if SBVariablesOptions exports the
// setter on the selected liblldb.

#include "lldb/API/SBVariablesOptions.h"

#include "layer_api.h"

namespace {

void layer_set_recognized(void *options, int enabled) {
  if (options == nullptr)
    return;
  static_cast<lldb::SBVariablesOptions *>(options)->SetIncludeRecognizedArguments(enabled != 0);
}

const gdb_mcp_lldb_layer *layer_api() {
  static const gdb_mcp_lldb_layer api = {
      GDB_MCP_LLDB_LAYER_ABI,
      8,
      "recognized_args",
      nullptr,
      nullptr,
      layer_set_recognized,
      nullptr,
      nullptr,
      nullptr,
  };
  return &api;
}

} // namespace

extern "C" const gdb_mcp_lldb_layer *lldb_layer_get_api() { return layer_api(); }
