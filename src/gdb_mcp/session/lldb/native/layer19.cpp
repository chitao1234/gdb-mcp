// LLDB 19+ layer: SBValue::GetValueAsAddress for tagged/pointed-to addresses.
//
// Built against LLDB 19 headers. The core only calls this for pointer-like
// values, so plain integers keep their scalar semantics.

#include "lldb/API/SBValue.h"

#include "layer_api.h"

namespace {

int layer_value_address(void *value_ptr, uint64_t *address) {
  if (value_ptr == nullptr || address == nullptr)
    return 0;
  lldb::SBValue *value = static_cast<lldb::SBValue *>(value_ptr);
  if (!value->IsValid())
    return 0;
  lldb::addr_t resolved = value->GetValueAsAddress();
  if (resolved == LLDB_INVALID_ADDRESS || resolved == 0)
    return 0;
  *address = static_cast<uint64_t>(resolved);
  return 1;
}

const gdb_mcp_lldb_layer *layer_api() {
  static const gdb_mcp_lldb_layer api = {
      GDB_MCP_LLDB_LAYER_ABI,
      19,
      "address_fixup",
      nullptr,
      nullptr,
      nullptr,
      nullptr,
      nullptr,
      layer_value_address,
  };
  return &api;
}

} // namespace

extern "C" const gdb_mcp_lldb_layer *lldb_layer_get_api() { return layer_api(); }
