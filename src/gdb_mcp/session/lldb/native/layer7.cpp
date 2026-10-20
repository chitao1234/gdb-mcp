// LLDB 7+ layer: core loading and stepping return SBError text.
//
// Built against LLDB 7 headers, linked against the same unversioned stub as the
// core. It is loaded only when the selected liblldb exports all of these symbols.

#include "lldb/API/SBError.h"
#include "lldb/API/SBProcess.h"
#include "lldb/API/SBTarget.h"
#include "lldb/API/SBThread.h"

#include <cstring>
#include <new>

#include "layer_api.h"

namespace {

void copy_error(const char *value, char *error, size_t error_size) {
  if (error == nullptr || error_size == 0)
    return;
  std::strncpy(error, value == nullptr ? "" : value, error_size - 1);
  error[error_size - 1] = '\0';
}

int layer_load_core(void *target_ptr, const char *path, void *out_process,
                    char *error, size_t error_size) {
  if (target_ptr == nullptr || path == nullptr || out_process == nullptr)
    return 0;
  lldb::SBTarget *target = static_cast<lldb::SBTarget *>(target_ptr);
  lldb::SBError lldb_error;
  lldb::SBProcess process = target->LoadCore(path, lldb_error);
  if (lldb_error.Fail() || !process.IsValid()) {
    copy_error(lldb_error.Fail() ? lldb_error.GetCString() : "could not load core file",
               error, error_size);
    return 0;
  }
  new (out_process) lldb::SBProcess(process);
  return 1;
}

int layer_step(void *thread_ptr, int kind, char *error, size_t error_size) {
  if (thread_ptr == nullptr)
    return 0;
  lldb::SBThread *thread = static_cast<lldb::SBThread *>(thread_ptr);
  lldb::SBError lldb_error;
  if (kind == 0)
    thread->StepInto(nullptr, UINT32_MAX, lldb_error, lldb::eOnlyThisThread);
  else if (kind == 1)
    thread->StepOver(lldb::eOnlyThisThread, lldb_error);
  else
    thread->StepOut(lldb_error);
  if (lldb_error.Fail()) {
    copy_error(lldb_error.GetCString(), error, error_size);
    return 0;
  }
  return 1;
}

const gdb_mcp_lldb_layer *layer_api() {
  static const gdb_mcp_lldb_layer api = {
      GDB_MCP_LLDB_LAYER_ABI,
      7,
      "error_overloads",
      layer_load_core,
      layer_step,
      nullptr,
      nullptr,
      nullptr,
      nullptr,
  };
  return &api;
}

} // namespace

extern "C" const gdb_mcp_lldb_layer *lldb_layer_get_api() { return layer_api(); }
