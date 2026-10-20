// Shared C ABI between the LLDB bridge core and its optional newer-API layers.
//
// A layer is compiled against a newer LLDB header set and linked against the
// same unversioned stub as the core. If the runtime liblldb lacks the symbols
// the layer needs, loading the layer fails and the core keeps its baseline
// behavior. No C++ types cross this boundary.

#ifndef GDB_MCP_LLDB_LAYER_API_H
#define GDB_MCP_LLDB_LAYER_API_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define GDB_MCP_LLDB_LAYER_ABI 1

typedef struct gdb_mcp_lldb_layer {
  uint32_t abi_version;
  uint32_t layer_version;
  const char *name;

  // layer 7: error-returning core/step operations.
  // Writes an SBProcess into out_process (the core owns the storage) and the
  // SBError text into error. Returns 1 on success, 0 on failure.
  int (*load_core)(void *target, const char *path, void *out_process,
                   char *error, size_t error_size);
  // kind: 0 = into, 1 = over, 2 = out. Returns 1 on success, 0 on failure.
  int (*step)(void *thread, int kind, char *error, size_t error_size);

  // layer 8: recognized arguments.
  void (*set_include_recognized_arguments)(void *variables_options, int enabled);

  // layer 16: siginfo. Returns 1 when a siginfo was available.
  // crash_json is a bounded flat JSON object, or an empty string.
  int (*get_siginfo)(void *thread, int *signo, int *code, int *has_fault_address,
                     uint64_t *fault_address, char *crash_json,
                     size_t crash_json_size);

  // layer 18: watchpoint access/spec.
  // Returns 1 when the watchpoint was valid.
  int (*watchpoint_access)(void *watchpoint, int *reads, int *writes,
                           char *spec, size_t spec_size);

  // layer 19: address fixup for tagged/pointed-to addresses.
  // Returns 1 when the value has an address.
  int (*value_address)(void *value, uint64_t *address);
} gdb_mcp_lldb_layer;

typedef const gdb_mcp_lldb_layer *(*gdb_mcp_lldb_layer_get_api_fn)(void);

#ifdef __cplusplus
}
#endif

#endif
