// LLDB 16+ layer: siginfo and compact extended crash information.
//
// Built against LLDB 16 headers. Uses SBThread::GetSiginfo and the structured
// crash information APIs, neither of which exists in the 4.0 baseline.

#include "lldb/API/SBProcess.h"
#include "lldb/API/SBStream.h"
#include "lldb/API/SBStructuredData.h"
#include "lldb/API/SBThread.h"
#include "lldb/API/SBValue.h"

#include <csignal>
#include <cstring>

#include "layer_api.h"

namespace {

uint64_t child_unsigned(lldb::SBValue value, const char *name, uint64_t fallback = 0) {
  lldb::SBValue child = value.GetChildMemberWithName(name);
  return child.IsValid() ? child.GetValueAsUnsigned(fallback) : fallback;
}

lldb::SBValue find_child_recursive(lldb::SBValue value, const char *name, int depth) {
  if (!value.IsValid() || depth > 4)
    return lldb::SBValue();
  lldb::SBValue direct = value.GetChildMemberWithName(name);
  if (direct.IsValid())
    return direct;
  uint32_t count = value.GetNumChildren();
  for (uint32_t index = 0; index < count; ++index) {
    lldb::SBValue found = find_child_recursive(value.GetChildAtIndex(index), name, depth + 1);
    if (found.IsValid())
      return found;
  }
  return lldb::SBValue();
}

uint64_t fault_address(lldb::SBValue info) {
  static const char *names[] = {"si_addr", "_addr", "si_addr_lsb"};
  for (const char *name : names) {
    lldb::SBValue value = find_child_recursive(info, name, 0);
    if (value.IsValid()) {
      uint64_t address = value.GetValueAsUnsigned(0);
      if (address != 0)
        return address;
    }
  }
  lldb::SBValue fields = info.GetChildMemberWithName("_sifields");
  if (fields.IsValid()) {
    lldb::SBValue fault = fields.GetChildMemberWithName("_sigfault");
    if (fault.IsValid()) {
      lldb::SBValue address = fault.GetChildMemberWithName("_addr");
      if (address.IsValid())
        return address.GetValueAsUnsigned(0);
    }
  }
  return 0;
}

void copy_json(const char *value, char *output, size_t output_size) {
  if (output == nullptr || output_size == 0)
    return;
  output[0] = '\0';
  if (value == nullptr)
    return;
  std::strncpy(output, value, output_size - 1);
  output[output_size - 1] = '\0';
}

int layer_siginfo(void *thread_ptr, int *signo, int *code, int *has_fault_address,
                  uint64_t *address, char *crash_json, size_t crash_json_size) {
  if (thread_ptr == nullptr)
    return 0;
  lldb::SBThread *thread = static_cast<lldb::SBThread *>(thread_ptr);
  lldb::SBValue info = thread->GetSiginfo();
  if (info.IsValid()) {
    if (signo != nullptr)
      *signo = static_cast<int>(child_unsigned(info, "si_signo"));
    if (code != nullptr)
      *code = static_cast<int>(child_unsigned(info, "si_code"));
    uint64_t fault = fault_address(info);
    if (has_fault_address != nullptr)
      *has_fault_address =
          fault != 0 || (signo != nullptr && (*signo == SIGSEGV || *signo == SIGBUS ||
                                              *signo == SIGILL || *signo == SIGFPE));
    if (address != nullptr)
      *address = fault;
  }
  if (crash_json != nullptr && crash_json_size > 0) {
    crash_json[0] = '\0';
    lldb::SBProcess process = thread->GetProcess();
    if (process.IsValid()) {
      lldb::SBStructuredData data = process.GetExtendedCrashInformation();
      if (data.IsValid()) {
        lldb::SBStream stream;
        if (data.GetAsJSON(stream).Success()) {
          const char *text = stream.GetData();
          copy_json(text, crash_json, crash_json_size);
        }
      }
    }
    if (crash_json[0] == '\0') {
      lldb::SBStream stream;
      if (thread->GetStopReasonExtendedInfoAsJSON(stream))
        copy_json(stream.GetData(), crash_json, crash_json_size);
    }
  }
  return info.IsValid() ? 1 : 0;
}

const gdb_mcp_lldb_layer *layer_api() {
  static const gdb_mcp_lldb_layer api = {
      GDB_MCP_LLDB_LAYER_ABI,
      16,
      "siginfo",
      nullptr,
      nullptr,
      nullptr,
      layer_siginfo,
      nullptr,
      nullptr,
  };
  return &api;
}

} // namespace

extern "C" const gdb_mcp_lldb_layer *lldb_layer_get_api() { return layer_api(); }
