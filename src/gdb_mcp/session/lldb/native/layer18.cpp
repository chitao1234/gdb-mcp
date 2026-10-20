// LLDB 18+ layer: watchpoint access modes and watch specification.
//
// Built against LLDB 18 headers. The core keeps its baseline watchpoint fields
// when this library cannot be loaded.

#include "lldb/API/SBWatchpoint.h"

#include <cstring>

#include "layer_api.h"

namespace {

void copy_text(const char *value, char *output, size_t output_size) {
  if (output == nullptr || output_size == 0)
    return;
  output[0] = '\0';
  if (value == nullptr)
    return;
  std::strncpy(output, value, output_size - 1);
  output[output_size - 1] = '\0';
}

int layer_watchpoint_access(void *watchpoint_ptr, int *reads, int *writes,
                            char *spec, size_t spec_size) {
  if (watchpoint_ptr == nullptr)
    return 0;
  lldb::SBWatchpoint *watchpoint = static_cast<lldb::SBWatchpoint *>(watchpoint_ptr);
  if (!watchpoint->IsValid())
    return 0;
  if (reads != nullptr)
    *reads = watchpoint->IsWatchingReads() ? 1 : 0;
  if (writes != nullptr)
    *writes = watchpoint->IsWatchingWrites() ? 1 : 0;
  copy_text(watchpoint->GetWatchSpec(), spec, spec_size);
  return 1;
}

const gdb_mcp_lldb_layer *layer_api() {
  static const gdb_mcp_lldb_layer api = {
      GDB_MCP_LLDB_LAYER_ABI,
      18,
      "watchpoint_introspection",
      nullptr,
      nullptr,
      nullptr,
      nullptr,
      layer_watchpoint_access,
      nullptr,
  };
  return &api;
}

} // namespace

extern "C" const gdb_mcp_lldb_layer *lldb_layer_get_api() { return layer_api(); }
