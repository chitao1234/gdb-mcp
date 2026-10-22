// LLDB bridge core.
//
// Compiled against the LLDB 4.0.1 SB API and linked against an unversioned
// liblldb stub. It speaks one JSON object per line over a private duplicate of
// stdout. Newer-API behavior lives in optional shared-library layers; a layer is
// used only if it loads against the selected liblldb.

#include "lldb/API/LLDB.h"
#include "layer_api.h"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>
#ifndef _WIN32
#include <dlfcn.h>
#endif

#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#ifdef _WIN32
#include <io.h>
#include <windows.h>
#endif

using namespace lldb;

namespace {

// ---------------------------------------------------------------------------
// JSON
// ---------------------------------------------------------------------------

struct Json {
  enum class Type { Null, Bool, Number, String, Array, Object };
  Type type = Type::Null;
  bool boolean = false;
  bool is_double = false;
  long long integer = 0;
  double number = 0.0;
  std::string string;
  std::vector<Json> array;
  std::map<std::string, Json> object;

  static Json null() { return Json(); }
  static Json boolean_value(bool value) {
    Json json;
    json.type = Type::Bool;
    json.boolean = value;
    return json;
  }
  static Json integer_value(long long value) {
    Json json;
    json.type = Type::Number;
    json.integer = value;
    return json;
  }
  static Json string_value(const std::string &value) {
    Json json;
    json.type = Type::String;
    json.string = value;
    return json;
  }
  static Json array_value() {
    Json json;
    json.type = Type::Array;
    return json;
  }
  static Json object_value() {
    Json json;
    json.type = Type::Object;
    return json;
  }
};

const Json &null_json() {
  static const Json value;
  return value;
}

const Json &member(const Json &object, const std::string &key) {
  if (object.type != Json::Type::Object)
    return null_json();
  auto it = object.object.find(key);
  return it == object.object.end() ? null_json() : it->second;
}

std::string as_string(const Json &value, const std::string &fallback = "") {
  if (value.type == Json::Type::String)
    return value.string;
  if (value.type == Json::Type::Number) {
    std::ostringstream out;
    if (value.is_double)
      out << value.number;
    else
      out << value.integer;
    return out.str();
  }
  return fallback;
}

long long as_integer(const Json &value, long long fallback = 0) {
  if (value.type == Json::Type::Number)
    return value.is_double ? static_cast<long long>(value.number) : value.integer;
  if (value.type == Json::Type::Bool)
    return value.boolean ? 1 : 0;
  if (value.type == Json::Type::String) {
    char *end = nullptr;
    long long parsed = std::strtoll(value.string.c_str(), &end, 0);
    return end != value.string.c_str() ? parsed : fallback;
  }
  return fallback;
}

bool as_bool(const Json &value, bool fallback = false) {
  if (value.type == Json::Type::Bool)
    return value.boolean;
  if (value.type == Json::Type::Number)
    return (value.is_double ? value.number : static_cast<double>(value.integer)) != 0;
  return fallback;
}

std::string json_escape(const std::string &value) {
  std::string out;
  out.reserve(value.size() + 8);
  for (unsigned char c : value) {
    switch (c) {
    case '"':
      out += "\\\"";
      break;
    case '\\':
      out += "\\\\";
      break;
    case '\b':
      out += "\\b";
      break;
    case '\f':
      out += "\\f";
      break;
    case '\n':
      out += "\\n";
      break;
    case '\r':
      out += "\\r";
      break;
    case '\t':
      out += "\\t";
      break;
    default:
      if (c < 0x20) {
        char buffer[7];
        std::snprintf(buffer, sizeof(buffer), "\\u%04x", c);
        out += buffer;
      } else {
        out += static_cast<char>(c);
      }
    }
  }
  return out;
}

void dump_json(const Json &value, std::string &out) {
  switch (value.type) {
  case Json::Type::Null:
    out += "null";
    break;
  case Json::Type::Bool:
    out += value.boolean ? "true" : "false";
    break;
  case Json::Type::Number:
    if (value.is_double) {
      char buffer[64];
      std::snprintf(buffer, sizeof(buffer), "%.17g", value.number);
      out += buffer;
    } else {
      char buffer[32];
      std::snprintf(buffer, sizeof(buffer), "%lld", value.integer);
      out += buffer;
    }
    break;
  case Json::Type::String:
    out += '"';
    out += json_escape(value.string);
    out += '"';
    break;
  case Json::Type::Array: {
    out += '[';
    bool first = true;
    for (const auto &item : value.array) {
      if (!first)
        out += ',';
      first = false;
      dump_json(item, out);
    }
    out += ']';
    break;
  }
  case Json::Type::Object: {
    out += '{';
    bool first = true;
    for (const auto &item : value.object) {
      if (!first)
        out += ',';
      first = false;
      out += '"';
      out += json_escape(item.first);
      out += "\":";
      dump_json(item.second, out);
    }
    out += '}';
    break;
  }
  }
}

class JsonParser {
public:
  explicit JsonParser(const std::string &text) : text_(text) {}

  Json parse() {
    skip_space();
    Json value = parse_value();
    skip_space();
    if (pos_ != text_.size())
      fail("trailing data");
    return value;
  }

private:
  [[noreturn]] void fail(const std::string &message) const {
    throw std::runtime_error("invalid JSON: " + message);
  }
  void skip_space() {
    while (pos_ < text_.size() && std::isspace(static_cast<unsigned char>(text_[pos_])))
      ++pos_;
  }
  bool consume(char expected) {
    if (pos_ < text_.size() && text_[pos_] == expected) {
      ++pos_;
      return true;
    }
    return false;
  }
  void literal(const char *value) {
    size_t length = std::strlen(value);
    if (text_.compare(pos_, length, value) != 0)
      fail(std::string("expected ") + value);
    pos_ += length;
  }
  Json parse_value() {
    if (pos_ >= text_.size())
      fail("unexpected end");
    char c = text_[pos_];
    if (c == 'n') {
      literal("null");
      return Json::null();
    }
    if (c == 't') {
      literal("true");
      return Json::boolean_value(true);
    }
    if (c == 'f') {
      literal("false");
      return Json::boolean_value(false);
    }
    if (c == '"')
      return Json::string_value(parse_string());
    if (c == '[')
      return parse_array();
    if (c == '{')
      return parse_object();
    if (c == '-' || (c >= '0' && c <= '9'))
      return parse_number();
    fail("unexpected character");
  }
  std::string parse_string() {
    if (!consume('"'))
      fail("expected string");
    std::string out;
    while (pos_ < text_.size()) {
      unsigned char c = static_cast<unsigned char>(text_[pos_++]);
      if (c == '"')
        return out;
      if (c != '\\') {
        out += static_cast<char>(c);
        continue;
      }
      if (pos_ >= text_.size())
        fail("unterminated escape");
      char escape = text_[pos_++];
      switch (escape) {
      case '"':
        out += '"';
        break;
      case '\\':
        out += '\\';
        break;
      case '/':
        out += '/';
        break;
      case 'b':
        out += '\b';
        break;
      case 'f':
        out += '\f';
        break;
      case 'n':
        out += '\n';
        break;
      case 'r':
        out += '\r';
        break;
      case 't':
        out += '\t';
        break;
      default:
        fail("unsupported escape");
      }
    }
    fail("unterminated string");
  }
  Json parse_number() {
    size_t start = pos_;
    if (text_[pos_] == '-')
      ++pos_;
    while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_])))
      ++pos_;
    bool is_double = false;
    if (pos_ < text_.size() && text_[pos_] == '.') {
      is_double = true;
      ++pos_;
      while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_])))
        ++pos_;
    }
    if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
      is_double = true;
      ++pos_;
      if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-'))
        ++pos_;
      while (pos_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[pos_])))
        ++pos_;
    }
    std::string token = text_.substr(start, pos_ - start);
    Json json;
    json.type = Json::Type::Number;
    if (is_double) {
      json.is_double = true;
      json.number = std::strtod(token.c_str(), nullptr);
    } else {
      json.integer = std::strtoll(token.c_str(), nullptr, 10);
    }
    return json;
  }
  Json parse_array() {
    consume('[');
    Json result = Json::array_value();
    skip_space();
    if (consume(']'))
      return result;
    while (true) {
      skip_space();
      result.array.push_back(parse_value());
      skip_space();
      if (consume(']'))
        return result;
      if (!consume(','))
        fail("expected ',' or ']'");
    }
  }
  Json parse_object() {
    consume('{');
    Json result = Json::object_value();
    skip_space();
    if (consume('}'))
      return result;
    while (true) {
      skip_space();
      if (pos_ >= text_.size() || text_[pos_] != '"')
        fail("expected object key");
      std::string key = parse_string();
      skip_space();
      if (!consume(':'))
        fail("expected ':'");
      skip_space();
      result.object.emplace(std::move(key), parse_value());
      skip_space();
      if (consume('}'))
        return result;
      if (!consume(','))
        fail("expected ',' or '}'");
    }
  }

  const std::string &text_;
  size_t pos_ = 0;
};

std::string dump_string(const Json &value) {
  std::string out;
  dump_json(value, out);
  return out;
}

Json make_array(const std::vector<Json> &items) {
  Json result = Json::array_value();
  result.array = items;
  return result;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

struct BridgeError : std::runtime_error {
  explicit BridgeError(const std::string &message, std::string code = "error")
      : std::runtime_error(message), code(std::move(code)) {}
  std::string code;
};

std::string S(const char *value) { return value == nullptr ? std::string() : value; }

std::string hex_address(uint64_t value) {
  char buffer[32];
  std::snprintf(buffer, sizeof(buffer), "0x%llx", static_cast<unsigned long long>(value));
  return buffer;
}

std::string trim(const std::string &value) {
  size_t start = value.find_first_not_of(" \t\r\n");
  if (start == std::string::npos)
    return "";
  size_t end = value.find_last_not_of(" \t\r\n");
  return value.substr(start, end - start + 1);
}

std::string file_path(SBFileSpec spec) {
  if (!spec.IsValid())
    return "";
  char buffer[4096];
  buffer[0] = '\0';
  spec.GetPath(buffer, sizeof(buffer));
  return buffer;
}

uint32_t frame_index(SBThread &thread) {
  SBFrame selected = thread.GetSelectedFrame();
  if (!selected.IsValid())
    return 0;
  uint32_t selected_id = selected.GetFrameID();
  for (uint32_t index = 0; index < thread.GetNumFrames(); ++index) {
    if (thread.GetFrameAtIndex(index).GetFrameID() == selected_id)
      return index;
  }
  return 0;
}

std::string stop_reason_name(StopReason reason) {
  switch (reason) {
  case eStopReasonInvalid:
  case eStopReasonNone:
    return "";
  case eStopReasonTrace:
    return "trace";
  case eStopReasonBreakpoint:
    return "breakpoint";
  case eStopReasonWatchpoint:
    return "watchpoint";
  case eStopReasonSignal:
    return "signal";
  case eStopReasonException:
    return "exception";
  case eStopReasonExec:
    return "exec";
  case eStopReasonPlanComplete:
    return "step";
  case eStopReasonThreadExiting:
    return "thread-exit";
  case eStopReasonInstrumentation:
    return "instrumentation";
  default:
    break;
  }
  switch (static_cast<int>(reason)) {
  case 12:
    return "fork";
  case 13:
    return "vfork";
  case 14:
    return "vfork-done";
  default:
    return "unknown";
  }
}

std::string stop_kind_name(StopReason reason) {
  switch (reason) {
  case eStopReasonBreakpoint:
    return "breakpoint";
  case eStopReasonWatchpoint:
    return "watchpoint";
  case eStopReasonSignal:
    return "signal";
  case eStopReasonException:
    return "exception";
  case eStopReasonPlanComplete:
  case eStopReasonTrace:
    return "step";
  case eStopReasonExec:
    return "exec";
  case eStopReasonThreadExiting:
    return "thread_exit";
  default:
    break;
  }
  switch (static_cast<int>(reason)) {
  case 12:
    return "fork";
  case 13:
  case 14:
    return "vfork";
  default:
    return "unknown";
  }
}

bool real_reason(StopReason reason) {
  return reason != eStopReasonNone && reason != eStopReasonInvalid;
}

bool fatal_signal(int signo) {
  switch (signo) {
  case SIGSEGV:
  case SIGBUS:
  case SIGILL:
  case SIGFPE:
  case SIGABRT:
  case SIGTRAP:
    return true;
  default:
    return false;
  }
}

struct LaunchConfig {
  std::string program;
  std::vector<std::string> args;
  std::vector<std::string> env;
  std::string working_dir;
};

struct Layer {
  void *handle = nullptr;
  const gdb_mcp_lldb_layer *api = nullptr;
  bool loaded() const { return api != nullptr; }
};

struct Bridge {
  SBDebugger debugger;
  SBListener listener;
  bool initialized = false;
  int selected_index = 0;
  LaunchConfig launch;
  Layer layer7;
  Layer layer8;
  Layer layer16;
  Layer layer18;
  Layer layer19;
};

Bridge *g_bridge = nullptr;

std::string bridge_directory() {
#ifdef _WIN32
  char buffer[4096];
  DWORD length = GetModuleFileNameA(nullptr, buffer, sizeof(buffer));
  if (length == 0)
    return ".";
  std::string path(buffer, length);
  size_t slash = path.find_last_of("\\/");
  return slash == std::string::npos ? "." : path.substr(0, slash);
#elif defined(__APPLE__)
  char buffer[4096];
  uint32_t size = sizeof(buffer);
  if (_NSGetExecutablePath(buffer, &size) != 0)
    return ".";
  std::string path(buffer);
  size_t slash = path.find_last_of('/');
  return slash == std::string::npos ? "." : path.substr(0, slash);
#else
  char buffer[4096];
  ssize_t length = readlink("/proc/self/exe", buffer, sizeof(buffer) - 1);
  if (length <= 0)
    return ".";
  buffer[length] = '\0';
  std::string path(buffer);
  size_t slash = path.find_last_of('/');
  return slash == std::string::npos ? "." : path.substr(0, slash);
#endif
}

void *open_library(const std::string &path) {
#ifdef _WIN32
  return reinterpret_cast<void *>(LoadLibraryA(path.c_str()));
#else
  return dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
#endif
}

void *library_symbol(void *handle, const char *name) {
#ifdef _WIN32
  return reinterpret_cast<void *>(GetProcAddress(reinterpret_cast<HMODULE>(handle), name));
#else
  return dlsym(handle, name);
#endif
}

int bridge_dup_stdout() {
#ifdef _WIN32
  return _dup(_fileno(stdout));
#else
  return ::dup(STDOUT_FILENO);
#endif
}

int bridge_open_null() {
#ifdef _WIN32
  return _open("NUL", _O_WRONLY);
#else
  return ::open("/dev/null", O_WRONLY);
#endif
}

void bridge_replace_stdout(int descriptor) {
#ifdef _WIN32
  _dup2(descriptor, _fileno(stdout));
  _close(descriptor);
#else
  ::dup2(descriptor, STDOUT_FILENO);
  ::close(descriptor);
#endif
}

long bridge_write(int descriptor, const char *data, size_t size) {
#ifdef _WIN32
  return _write(descriptor, data, static_cast<unsigned int>(size));
#else
  return ::write(descriptor, data, size);
#endif
}

void bridge_close(int descriptor) {
#ifdef _WIN32
  _close(descriptor);
#else
  ::close(descriptor);
#endif
}

void close_library(void *handle) {
#ifdef _WIN32
  if (handle)
    FreeLibrary(reinterpret_cast<HMODULE>(handle));
#else
  if (handle)
    dlclose(handle);
#endif
}

std::string library_suffix() {
#ifdef _WIN32
  return ".dll";
#elif defined(__APPLE__)
  return ".dylib";
#else
  return ".so";
#endif
}

void load_layer(Layer &layer, int version, const char *name) {
  std::string directory = bridge_directory();
  const char *configured = std::getenv("LLDB_BRIDGE_LAYER_DIR");
  if (configured != nullptr && configured[0] != '\0')
    directory = configured;
  std::string base = directory + "/lldb_layer" + std::to_string(version) + "_" + name;
  std::string path = base + library_suffix();
  void *handle = open_library(path);
  if (handle == nullptr && library_suffix() == ".so") {
    path = base + ".dylib";
    handle = open_library(path);
  }
  if (handle == nullptr)
    return;
  auto get_api = reinterpret_cast<gdb_mcp_lldb_layer_get_api_fn>(
      library_symbol(handle, "lldb_layer_get_api"));
  if (get_api == nullptr) {
    close_library(handle);
    return;
  }
  const gdb_mcp_lldb_layer *api = get_api();
  if (api == nullptr || api->abi_version != GDB_MCP_LLDB_LAYER_ABI) {
    close_library(handle);
    return;
  }
  layer.handle = handle;
  layer.api = api;
}

void load_layers() {
  load_layer(g_bridge->layer7, 7, "errors");
  load_layer(g_bridge->layer8, 8, "args");
  load_layer(g_bridge->layer16, 16, "siginfo");
  load_layer(g_bridge->layer18, 18, "watchpoints");
  load_layer(g_bridge->layer19, 19, "address");
}

SBTarget current_target() {
  SBTarget target = g_bridge->debugger.GetSelectedTarget();
  if (target.IsValid())
    return target;
  uint32_t count = g_bridge->debugger.GetNumTargets();
  if (count == 0)
    return SBTarget();
  target = g_bridge->debugger.GetTargetAtIndex(g_bridge->selected_index);
  if (!target.IsValid())
    target = g_bridge->debugger.GetTargetAtIndex(0);
  if (target.IsValid())
    g_bridge->debugger.SetSelectedTarget(target);
  return target;
}

SBTarget require_target() {
  SBTarget target = current_target();
  if (!target.IsValid())
    throw BridgeError("No target is loaded", "not_found");
  return target;
}

SBProcess current_process() {
  SBTarget target = current_target();
  return target.IsValid() ? target.GetProcess() : SBProcess();
}

bool target_loaded(SBTarget &target) {
  if (!target.IsValid())
    return false;
  SBFileSpec executable = target.GetExecutable();
  return executable.IsValid() || target.GetProcess().IsValid();
}

void select_target_index(int index) {
  uint32_t count = g_bridge->debugger.GetNumTargets();
  if (count == 0)
    throw BridgeError("No target is loaded", "not_found");
  if (index < 0)
    index = 0;
  if (index >= static_cast<int>(count))
    index = static_cast<int>(count) - 1;
  SBTarget target = g_bridge->debugger.GetTargetAtIndex(static_cast<uint32_t>(index));
  if (!target.IsValid())
    throw BridgeError("Could not select target", "not_found");
  g_bridge->debugger.SetSelectedTarget(target);
  g_bridge->selected_index = index;
}

SBTarget create_target(const std::string &program, bool make_current) {
  SBTarget target;
  if (program.empty())
    target = g_bridge->debugger.CreateTarget(nullptr);
  else
    target = g_bridge->debugger.CreateTarget(program.c_str());
  if (!target.IsValid())
    throw BridgeError(program.empty() ? "Could not create an empty target"
                                      : "Could not load executable: " + program,
                      "not_found");
  if (make_current) {
    g_bridge->debugger.SetSelectedTarget(target);
    g_bridge->selected_index = static_cast<int>(g_bridge->debugger.GetNumTargets()) - 1;
  }
  return target;
}

void drain_events() {
  SBEvent event;
  while (g_bridge->listener.WaitForEvent(0, event)) {
  }
  while (g_bridge->listener.GetNextEvent(event)) {
  }
}

SBProcess ensure_thread(SBThread &thread, SBFrame &frame, bool select_stopping = true) {
  SBProcess process = current_process();
  if (!process.IsValid())
    return process;
  SBThread selected = process.GetSelectedThread();
  thread = selected;
  if (select_stopping) {
    bool found = selected.IsValid() && real_reason(selected.GetStopReason());
    if (!found) {
      for (uint32_t index = 0; index < process.GetNumThreads(); ++index) {
        SBThread candidate = process.GetThreadAtIndex(index);
        if (candidate.IsValid() && real_reason(candidate.GetStopReason())) {
          thread = candidate;
          process.SetSelectedThread(thread);
          found = true;
          break;
        }
      }
    }
  }
  if (!thread.IsValid() && process.GetNumThreads() > 0) {
    thread = process.GetThreadAtIndex(0);
    if (thread.IsValid())
      process.SetSelectedThread(thread);
  }
  if (thread.IsValid()) {
    frame = thread.GetSelectedFrame();
    if (!frame.IsValid() && thread.GetNumFrames() > 0) {
      frame = thread.GetFrameAtIndex(0);
      thread.SetSelectedFrame(0);
    }
  }
  return process;
}

// ---------------------------------------------------------------------------
// Records
// ---------------------------------------------------------------------------

Json frame_json(SBFrame &frame, uint32_t level) {
  Json out = Json::object_value();
  out.object["level"] = Json::integer_value(level);
  addr_t pc = frame.GetPC();
  if (pc != LLDB_INVALID_ADDRESS && pc != 0)
    out.object["addr"] = Json::string_value(hex_address(pc));
  std::string name = S(frame.GetFunctionName());
  if (name.empty())
    name = S(frame.GetSymbol().GetName());
  if (!name.empty())
    out.object["func"] = Json::string_value(name);
  SBLineEntry entry = frame.GetLineEntry();
  std::string path = file_path(entry.GetFileSpec());
  if (!path.empty()) {
    out.object["file"] = Json::string_value(path);
    out.object["fullname"] = Json::string_value(path);
  }
  if (entry.GetLine() != 0 && entry.GetLine() != UINT32_MAX)
    out.object["line"] = Json::integer_value(entry.GetLine());
  return out;
}

Json value_json(SBValue &value) {
  Json out = Json::object_value();
  out.object["name"] = Json::string_value(S(value.GetName()));
  out.object["type"] = Json::string_value(S(value.GetTypeName()));
  std::string text = S(value.GetValue());
  std::string summary = S(value.GetSummary());
  if (text.empty())
    text = summary;
  out.object["value"] = Json::string_value(text);
  out.object["summary"] = Json::string_value(summary);
  out.object["children_count"] = Json::integer_value(value.GetNumChildren());
  addr_t address = value.GetLoadAddress();
  if (address != LLDB_INVALID_ADDRESS && address != 0)
    out.object["address"] = Json::string_value(hex_address(address));
  return out;
}

Json thread_json(SBThread &thread, SBProcess &process) {
  Json out = Json::object_value();
  out.object["id"] = Json::integer_value(thread.GetIndexID());
  out.object["target-id"] = Json::integer_value(static_cast<long long>(thread.GetThreadID()));
  std::string name = S(thread.GetName());
  if (!name.empty())
    out.object["name"] = Json::string_value(name);
  std::string reason = stop_reason_name(thread.GetStopReason());
  if (!reason.empty()) {
    out.object["stop_reason"] = Json::string_value(reason);
    out.object["stop_kind"] = Json::string_value(stop_kind_name(thread.GetStopReason()));
  }
  out.object["state"] = Json::string_value(process.GetState() == eStateStopped ? "stopped"
                                        : process.GetState() == eStateRunning ? "running"
                                                                              : "unknown");
  return out;
}

const char *execution_state_name(StateType state) {
  switch (state) {
  case eStateStopped:
  case eStateCrashed:
  case eStateSuspended:
    return "paused";
  case eStateRunning:
  case eStateStepping:
  case eStateAttaching:
  case eStateLaunching:
    return "running";
  case eStateExited:
  case eStateDetached:
    return "exited";
  case eStateInvalid:
  case eStateUnloaded:
  case eStateConnected:
    return "not_started";
  }
  return "unknown";
}

Json status_json(const char *forced_reason = nullptr, bool *timed_out = nullptr) {
  drain_events();
  Json out = Json::object_value();
  SBTarget target = current_target();
  SBProcess process = target.IsValid() ? target.GetProcess() : SBProcess();
  bool loaded = target_loaded(target);
  StateType state = process.IsValid() ? process.GetState() : eStateInvalid;
  std::string execution = execution_state_name(state);

  out.object["target_loaded"] = Json::boolean_value(loaded);
  out.object["execution_state"] = Json::string_value(execution);
  out.object["is_running"] = Json::boolean_value(g_bridge->initialized);
  uint32_t target_count = g_bridge->debugger.GetNumTargets();
  out.object["inferior_count"] = Json::integer_value(static_cast<long long>(target_count));
  out.object["current_inferior_id"] =
      target_count == 0 ? Json::integer_value(0)
                        : Json::integer_value(g_bridge->selected_index + 1);
  out.object["timed_out"] = Json::boolean_value(timed_out != nullptr && *timed_out);
  if (state == eStateCrashed)
    out.object["state_detail"] = Json::string_value("crashed");
  if (process.IsValid()) {
    out.object["pid"] = Json::integer_value(static_cast<long long>(process.GetProcessID()));
    out.object["stop_id"] = Json::integer_value(process.GetStopID());
    int exit_status = process.GetExitStatus();
    if (state == eStateExited && exit_status >= 0)
      out.object["exit_code"] = Json::integer_value(exit_status);
  }

  SBThread thread;
  SBFrame frame;
  ensure_thread(thread, frame);
  std::string reason;
  bool crashed = state == eStateCrashed;
  Json data = Json::array_value();
  if (state == eStateExited) {
    reason = "exited";
  } else if (thread.IsValid() && (real_reason(thread.GetStopReason()) || forced_reason != nullptr)) {
    reason = forced_reason != nullptr ? std::string(forced_reason)
                                      : stop_reason_name(thread.GetStopReason());
    for (size_t index = 0; index < thread.GetStopReasonDataCount(); ++index)
      data.array.push_back(
          Json::integer_value(static_cast<long long>(thread.GetStopReasonDataAtIndex(index))));
    StopReason native = thread.GetStopReason();
    if (native == eStopReasonSignal && thread.GetStopReasonDataCount() > 0) {
      int signal_number = static_cast<int>(thread.GetStopReasonDataAtIndex(0));
      out.object["signal"] = Json::integer_value(signal_number);
      if (fatal_signal(signal_number))
        crashed = true;
      SBUnixSignals signals = process.GetUnixSignals();
      std::string signal_name = S(signals.GetSignalAsCString(signal_number));
      if (!signal_name.empty())
        out.object["signal_name"] = Json::string_value(signal_name);
    }
    if (native == eStopReasonBreakpoint && thread.GetStopReasonDataCount() > 0) {
      long long breakpoint_id = static_cast<long long>(thread.GetStopReasonDataAtIndex(0));
      if (breakpoint_id > 0)
        out.object["breakpoint_id"] = Json::integer_value(breakpoint_id);
    }
    if (native == eStopReasonWatchpoint && thread.GetStopReasonDataCount() > 0) {
      long long watchpoint_id = static_cast<long long>(thread.GetStopReasonDataAtIndex(0));
      if (watchpoint_id > 0)
        out.object["watchpoint_id"] = Json::integer_value(watchpoint_id);
    }
    if ((native == eStopReasonSignal || native == eStopReasonException) &&
        g_bridge->layer16.loaded() && g_bridge->layer16.api->get_siginfo != nullptr) {
      int signo = 0, code = 0, has_fault = 0;
      uint64_t fault = 0;
      char crash_json[2048] = {0};
      if (g_bridge->layer16.api->get_siginfo(
              &thread, &signo, &code, &has_fault, &fault, crash_json,
              sizeof(crash_json))) {
        out.object["signal_code"] = Json::integer_value(code);
        if (has_fault)
          out.object["fault_address"] = Json::string_value(hex_address(fault));
        if (crash_json[0] != '\0') {
          try {
            out.object["crash"] = JsonParser(crash_json).parse();
          } catch (const std::exception &) {
          }
        }
      }
    }
  }
  if (!reason.empty())
    out.object["stop_reason"] = Json::string_value(reason);
  if (crashed)
    out.object["state_detail"] = Json::string_value("crashed");
  std::string kind;
  if (reason == "exited")
    kind = "exit";
  else if (reason == "interrupt")
    kind = "pause";
  else if (!reason.empty() && thread.IsValid())
    kind = stop_kind_name(thread.GetStopReason());
  out.object["stop_kind"] = kind.empty() ? Json::null() : Json::string_value(kind);
  out.object["stop_data"] = data;
  if (thread.IsValid()) {
    out.object["current_thread_id"] = Json::integer_value(thread.GetIndexID());
    out.object["thread"] = thread_json(thread, process);
    if (frame.IsValid()) {
      uint32_t level = frame_index(thread);
      out.object["current_frame"] = Json::integer_value(level);
      out.object["frame"] = frame_json(frame, level);
    }
    SBValue return_value = thread.GetStopReturnValue();
    if (return_value.IsValid()) {
      std::string text = S(return_value.GetValue());
      if (text.empty())
        text = S(return_value.GetSummary());
      if (!text.empty())
        out.object["return_value"] = Json::string_value(text);
    }
  }
  return out;
}

bool terminal_state(StateType state) {
  return state == eStateStopped || state == eStateCrashed || state == eStateExited ||
         state == eStateDetached || state == eStateSuspended;
}

Json wait_for_terminal(long long timeout_ms, const char *forced_reason = nullptr) {
  SBProcess process = current_process();
  if (!process.IsValid())
    return status_json(forced_reason);
  auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  bool timed_out = false;
  while (true) {
    drain_events();
    if (terminal_state(process.GetState()))
      break;
    if (std::chrono::steady_clock::now() >= deadline) {
      timed_out = true;
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  return status_json(forced_reason, &timed_out);
}

Json target_json(SBTarget &target, int index) {
  Json out = Json::object_value();
  out.object["inferior_id"] = Json::integer_value(index + 1);
  out.object["is_current"] = Json::boolean_value(index == g_bridge->selected_index);
  std::string path = file_path(target.GetExecutable());
  out.object["display"] = Json::string_value(path.empty() ? "<no executable>" : path);
  out.object["description"] = Json::string_value(path.empty() ? "<no executable>" : path);
  out.object["executable"] = path.empty() ? Json::null() : Json::string_value(path);
  SBProcess process = target.GetProcess();
  if (process.IsValid()) {
    StateType state = process.GetState();
    out.object["execution_state"] = Json::string_value(execution_state_name(state));
    if (state == eStateExited && process.GetExitStatus() >= 0)
      out.object["exit_code"] = Json::integer_value(process.GetExitStatus());
  } else {
    out.object["execution_state"] = Json::string_value("not_started");
  }
  return out;
}

// ---------------------------------------------------------------------------
// Layer-aware operations
// ---------------------------------------------------------------------------

Json load_core(const Json &request) {
  SBTarget target = require_target();
  std::string path = as_string(member(request, "path"));
  if (path.empty())
    throw BridgeError("A core file path is required", "validation_error");
  SBProcess process;
  std::string error_text;
  bool ok = false;
  if (g_bridge->layer7.loaded() && g_bridge->layer7.api->load_core != nullptr) {
    char error[2048] = {0};
    ok = g_bridge->layer7.api->load_core(&target, path.c_str(), &process, error,
                                         sizeof(error)) != 0;
    error_text = error;
  } else {
    process = target.LoadCore(path.c_str());
    ok = process.IsValid();
    if (!ok)
      error_text = "could not load core file: " + path;
  }
  if (!ok)
    throw BridgeError(error_text.empty() ? "could not load core file: " + path : error_text);
  return status_json();
}

Json step_thread(const std::string &kind) {
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "not_started");
  if (process.GetState() == eStateExited || process.GetState() == eStateDetached)
    throw BridgeError("The program has exited", "not_started");
  if (process.GetState() == eStateRunning || process.GetState() == eStateStepping)
    throw BridgeError("The program is already running", "not_started");
  SBThread thread;
  SBFrame frame;
  ensure_thread(thread, frame, false);
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  int code = kind == "into" ? 0
             : kind == "over" ? 1
             : kind == "out" ? 2
             : kind == "instruction_into" ? 3
                                            : 4;
  if (g_bridge->layer7.loaded() && g_bridge->layer7.api->step != nullptr) {
    char error[2048] = {0};
    if (!g_bridge->layer7.api->step(&thread, code, error, sizeof(error)))
      throw BridgeError(error[0] ? error : "step failed");
  } else {
    if (code == 0)
      thread.StepInto(eOnlyThisThread);
    else if (code == 1)
      thread.StepOver(eOnlyThisThread);
    else if (code == 2)
      thread.StepOut();
    else if (code == 3)
      thread.StepInstruction(false);
    else
      thread.StepInstruction(true);
  }
  return status_json();
}

Json return_from_frame(const Json &request) {
  SBThread thread;
  SBFrame frame;
  ensure_thread(thread, frame, false);
  if (!thread.IsValid() || !frame.IsValid())
    throw BridgeError("No frame is selected", "no_stack_frame");
  std::string expression = as_string(member(request, "value"));
  SBValue value;
  if (!expression.empty()) {
    value = frame.EvaluateExpression(expression.c_str());
    SBError evaluation = value.GetError();
    if (!value.IsValid() || evaluation.Fail())
      throw BridgeError(
          evaluation.Fail() ? S(evaluation.GetCString())
                            : "return value did not evaluate");
  }
  SBError error = thread.ReturnFromFrame(frame, value);
  if (error.Fail())
    throw BridgeError(S(error.GetCString()));
  Json out = status_json();
  if (value.IsValid()) {
    std::string text = S(value.GetValue());
    if (text.empty())
      text = S(value.GetSummary());
    if (!text.empty())
      out.object["return_value"] = Json::string_value(text);
  }
  return out;
}

std::string quote_lldb(const std::string &value) {
  std::string quoted = "\"";
  for (char character : value) {
    if (character == '\\' || character == '"')
      quoted += '\\';
    quoted += character;
  }
  return quoted + '"';
}

Json execute_location(const Json &request) {
  bool relocate = as_bool(member(request, "relocate"), false);
  std::string kind = as_string(member(request, "kind"));
  std::string text = relocate ? "thread jump --force" : "thread until";
  std::string error;
  std::string output;
  bool succeeded;
  if (!relocate && kind == "file_line") {
    SBThread thread;
    SBFrame frame;
    ensure_thread(thread, frame, false);
    frame = thread.GetFrameAtIndex(0);
    SBFileSpec file(as_string(member(request, "file")).c_str(), false);
    SBError result = thread.StepOverUntil(
        frame, file, static_cast<uint32_t>(as_integer(member(request, "line"), 0)));
    succeeded = result.Success();
    error = S(result.GetCString());
    text = "SBThread::StepOverUntil";
  } else {
    if (kind == "address")
      text += " --address " + quote_lldb(as_string(member(request, "address")));
    else {
      if (kind == "file_line")
        text += " --file " + quote_lldb(as_string(member(request, "file")));
      text += (relocate ? " --line " : " -- ") +
              as_string(member(request, "line"));
    }
    SBCommandReturnObject result;
    g_bridge->debugger.GetCommandInterpreter().HandleCommand(text.c_str(), result, false);
    succeeded = result.Succeeded();
    error = S(result.GetError());
    output = S(result.GetOutput());
  }
  bool relocated = relocate && succeeded;
  if (relocated) {
    SBError continued = current_process().Continue();
    succeeded = continued.Success();
    if (!succeeded)
      error += S(continued.GetCString());
  }
  Json out = status_json();
  out.object["command"] = Json::string_value(text);
  out.object["console_output"] = Json::string_value(output);
  out.object["succeeded"] = Json::boolean_value(succeeded);
  out.object["error"] = Json::string_value(error);
  out.object["relocated"] = Json::boolean_value(relocated);
  return out;
}

Json connect_remote(const Json &request) {
  std::string url = as_string(member(request, "endpoint"));
  if (url.empty())
    throw BridgeError("A remote endpoint is required", "validation_error");
  SBTarget target = current_target();
  if (!target.IsValid())
    target = create_target("", true);
  std::string plugin = as_string(member(request, "plugin"), "gdb-remote");
  SBError error;
  SBProcess process = target.ConnectRemote(g_bridge->listener, url.c_str(),
                                           plugin.c_str(), error);
  if (error.Fail() || !process.IsValid())
    throw BridgeError(error.Fail() ? S(error.GetCString()) : "remote connect failed");
  return status_json();
}

Json attach_process(const Json &request) {  SBTarget target = current_target();
  if (!target.IsValid())
    target = create_target("", true);
  long long pid = as_integer(member(request, "pid"), 0);
  if (pid <= 0)
    throw BridgeError("A positive pid is required", "validation_error");
  SBAttachInfo info(static_cast<lldb::pid_t>(pid));
  info.SetProcessPluginName("gdb-remote");
  SBError error;
  SBProcess process = target.Attach(info, error);
  if (error.Fail() || !process.IsValid())
    throw BridgeError(error.Fail() ? S(error.GetCString()) : "attach failed");
  return status_json();
}

Json launch(const Json &request) {
  SBTarget target = require_target();
  std::string program = file_path(target.GetExecutable());
  if (program.empty())
    throw BridgeError("No executable is loaded; start the session with a program", "not_started");
  std::vector<std::string> args = g_bridge->launch.args;
  if (member(request, "args").type == Json::Type::Array) {
    args.clear();
    for (const auto &item : member(request, "args").array)
      args.push_back(as_string(item));
  }
  SBProcess old = target.GetProcess();
  if (old.IsValid() && old.GetState() != eStateExited && old.GetState() != eStateDetached)
    old.Kill();
  SBLaunchInfo info = target.GetLaunchInfo();
  std::vector<std::string> argv_store;
  argv_store.push_back(program);
  for (const auto &arg : args)
    argv_store.push_back(arg);
  std::vector<const char *> argv;
  for (const auto &arg : argv_store)
    argv.push_back(arg.c_str());
  argv.push_back(nullptr);
  info.SetArguments(argv.data(), false);
  if (!g_bridge->launch.working_dir.empty())
    info.SetWorkingDirectory(g_bridge->launch.working_dir.c_str());
  if (!g_bridge->launch.env.empty()) {
    std::vector<const char *> envp;
    for (const auto &entry : g_bridge->launch.env)
      envp.push_back(entry.c_str());
    envp.push_back(nullptr);
    info.SetEnvironmentEntries(envp.data(), false);
  }
  SBError error;
  SBProcess process = target.Launch(info, error);
  if (error.Fail() || !process.IsValid())
    throw BridgeError(error.Fail() ? S(error.GetCString()) : "process launch failed");
  return status_json();
}

Json configure_launch(const Json &request) {
  g_bridge->launch.program = as_string(member(request, "program"));
  g_bridge->launch.working_dir = as_string(member(request, "working_dir"));
  g_bridge->launch.args.clear();
  for (const auto &item : member(request, "args").array)
    g_bridge->launch.args.push_back(as_string(item));
  g_bridge->launch.env.clear();
  for (const auto &item : member(request, "env").array)
    g_bridge->launch.env.push_back(as_string(item));
  return Json::object_value();
}

Json setting_set(const Json &request) {
  std::string name = as_string(member(request, "name"));
  std::string value = as_string(member(request, "value"));
  SBError error =
      SBDebugger::SetInternalVariable(name.c_str(), value.c_str(), g_bridge->debugger.GetInstanceName());
  if (error.Fail())
    throw BridgeError(S(error.GetCString()));
  return Json::object_value();
}

Json setting_get(const Json &request) {
  std::string name = as_string(member(request, "name"));
  SBStringList values =
      SBDebugger::GetInternalVariableValue(name.c_str(), g_bridge->debugger.GetInstanceName());
  Json out = Json::object_value();
  Json items = Json::array_value();
  for (uint32_t index = 0; index < values.GetSize(); ++index)
    items.array.push_back(Json::string_value(S(values.GetStringAtIndex(index))));
  out.object["values"] = items;
  return out;
}

// ---------------------------------------------------------------------------
// Breakpoints
// ---------------------------------------------------------------------------

Json location_json(SBBreakpointLocation location, SBTarget &target) {
  Json out = Json::object_value();
  SBAddress address = location.GetAddress();
  addr_t load = location.GetLoadAddress();
  if (load == LLDB_INVALID_ADDRESS || load == 0)
    load = address.GetLoadAddress(target);
  if (load != LLDB_INVALID_ADDRESS && load != 0)
    out.object["addr"] = Json::string_value(hex_address(load));
  SBLineEntry line = address.GetLineEntry();
  std::string path = file_path(line.GetFileSpec());
  if (!path.empty()) {
    out.object["file"] = Json::string_value(path);
    out.object["fullname"] = Json::string_value(path);
  }
  if (line.GetLine() != 0 && line.GetLine() != UINT32_MAX)
    out.object["line"] = Json::integer_value(line.GetLine());
  if (line.GetColumn() != 0 && line.GetColumn() != UINT32_MAX)
    out.object["column"] = Json::integer_value(line.GetColumn());
  std::string name = S(address.GetFunction().GetName());
  if (name.empty())
    name = S(address.GetSymbol().GetName());
  if (!name.empty())
    out.object["func"] = Json::string_value(name);
  out.object["resolved"] =
      Json::boolean_value(load != LLDB_INVALID_ADDRESS && load != 0);
  return out;
}

Json breakpoint_json(SBBreakpoint breakpoint, SBTarget &target) {
  Json out = Json::object_value();
  out.object["number"] = Json::string_value(std::to_string(breakpoint.GetID()));
  out.object["type"] = Json::string_value("breakpoint");
  out.object["enabled"] = Json::boolean_value(breakpoint.IsEnabled());
  out.object["times"] = Json::integer_value(breakpoint.GetHitCount());
  out.object["verified"] = Json::boolean_value(breakpoint.GetNumResolvedLocations() > 0);
  out.object["condition"] = Json::string_value(S(breakpoint.GetCondition()));
  Json locations = Json::array_value();
  for (uint32_t index = 0; index < breakpoint.GetNumLocations(); ++index)
    locations.array.push_back(location_json(breakpoint.GetLocationAtIndex(index), target));
  out.object["locations"] = locations;
  return out;
}

Json watch_json(SBWatchpoint watchpoint, const std::string &spec, const char *access) {
  Json out = Json::object_value();
  out.object["number"] = Json::string_value(std::to_string(watchpoint.GetID()));
  out.object["type"] = Json::string_value("watchpoint");
  out.object["enabled"] = Json::boolean_value(watchpoint.IsEnabled());
  out.object["times"] = Json::integer_value(watchpoint.GetHitCount());
  out.object["condition"] = Json::string_value(S(watchpoint.GetCondition()));
  Json watch = Json::object_value();
  if (access != nullptr)
    watch.object["access"] = Json::string_value(access);
  addr_t address = watchpoint.GetWatchAddress();
  if (address != LLDB_INVALID_ADDRESS)
    watch.object["address"] = Json::string_value(hex_address(address));
  watch.object["size"] = Json::integer_value(static_cast<long long>(watchpoint.GetWatchSize()));
  if (g_bridge->layer18.loaded() && g_bridge->layer18.api->watchpoint_access != nullptr) {
    int reads = 0, writes = 0;
    char native_spec[2048] = {0};
    if (g_bridge->layer18.api->watchpoint_access(&watchpoint, &reads, &writes,
                                                 native_spec, sizeof(native_spec))) {
      if (reads && writes)
        watch.object["access"] = Json::string_value("access");
      else if (reads)
        watch.object["access"] = Json::string_value("read");
      else if (writes)
        watch.object["access"] = Json::string_value("write");
      if (native_spec[0] != '\0')
        watch.object["spec"] = Json::string_value(native_spec);
    }
  }
  if (!spec.empty() && !watch.object.count("spec"))
    watch.object["spec"] = Json::string_value(spec);
  out.object["watch"] = watch;
  out.object["locations"] = Json::array_value();
  return out;
}

bool resolve_address(const std::string &expression, uint64_t &address, std::string &error);

Json breakpoint_create(const Json &request) {
  SBTarget target = require_target();
  std::string mode = as_string(member(request, "mode"), "function");
  std::string condition = as_string(member(request, "condition"));
  bool temporary = as_bool(member(request, "temporary"), false);
  SBBreakpoint breakpoint;
  if (mode == "function") {
    std::string name = as_string(member(request, "name"));
    if (name.empty())
      throw BridgeError("A breakpoint name is required", "validation_error");
    breakpoint = target.BreakpointCreateByName(name.c_str(), nullptr);
  } else if (mode == "location") {
    std::string file = as_string(member(request, "file"));
    long long line = as_integer(member(request, "line"), 0);
    if (file.empty() || line <= 0)
      throw BridgeError("A file and positive line are required", "validation_error");
    breakpoint = target.BreakpointCreateByLocation(file.c_str(), static_cast<uint32_t>(line));
  } else if (mode == "address") {
    std::string expression = as_string(member(request, "address"));
    uint64_t address = 0;
    std::string error;
    if (!resolve_address(expression, address, error))
      throw BridgeError(error, "not_found");
    breakpoint = target.BreakpointCreateByAddress(static_cast<addr_t>(address));
  } else if (mode == "exception") {
    breakpoint = target.BreakpointCreateForException(
        eLanguageTypeC_plus_plus, as_bool(member(request, "catch_exception"), false),
        as_bool(member(request, "throw_exception"), true));
  } else {
    throw BridgeError("Unknown breakpoint mode: " + mode, "validation_error");
  }
  if (!breakpoint.IsValid())
    throw BridgeError("LLDB did not create a breakpoint", "not_found");
  if (!condition.empty())
    breakpoint.SetCondition(condition.c_str());
  if (temporary)
    breakpoint.SetOneShot(true);
  Json result = breakpoint_json(breakpoint, target);
  result.object["original_location"] = Json::string_value(as_string(member(request, "original")));
  return result;
}

Json breakpoint_list() {
  SBTarget target = require_target();
  Json items = Json::array_value();
  for (uint32_t index = 0; index < target.GetNumBreakpoints(); ++index) {
    SBBreakpoint breakpoint = target.GetBreakpointAtIndex(index);
    if (breakpoint.IsValid() && !breakpoint.IsInternal())
      items.array.push_back(breakpoint_json(breakpoint, target));
  }
  Json out = Json::object_value();
  out.object["breakpoints"] = items;
  return out;
}

Json watchpoint_create(const Json &request) {
  std::string expression = as_string(member(request, "expression"));
  bool read = as_bool(member(request, "read"), false);
  bool write = as_bool(member(request, "write"), true);
  std::string access = as_string(member(request, "access"), write && read ? "access" : read ? "read" : "write");
  SBThread thread;
  SBFrame frame;
  ensure_thread(thread, frame, false);
  SBValue value;
  if (frame.IsValid())
    value = frame.EvaluateExpression(expression.c_str());
  else
    value = require_target().EvaluateExpression(expression.c_str());
  SBError error = value.GetError();
  if (!value.IsValid() || error.Fail())
    throw BridgeError(error.Fail() ? S(error.GetCString()) : "could not evaluate watch expression",
                      "not_found");
  SBError watch_error;
  SBWatchpoint watchpoint = value.Watch(false, read, write, watch_error);
  if (watch_error.Fail() || !watchpoint.IsValid())
    throw BridgeError(watch_error.Fail() ? S(watch_error.GetCString()) : "could not create watchpoint");
  return watch_json(watchpoint, expression, access.c_str());
}

Json watchpoint_list() {
  SBTarget target = require_target();
  Json items = Json::array_value();
  for (uint32_t index = 0; index < target.GetNumWatchpoints(); ++index) {
    SBWatchpoint watchpoint = target.GetWatchpointAtIndex(index);
    if (watchpoint.IsValid())
      items.array.push_back(watch_json(watchpoint, "", nullptr));
  }
  Json out = Json::object_value();
  out.object["watchpoints"] = items;
  return out;
}

Json breakpoint_find(long long id, bool watch) {
  SBTarget target = require_target();
  if (watch) {
    SBWatchpoint watchpoint = target.FindWatchpointByID(static_cast<watch_id_t>(id));
    if (!watchpoint.IsValid())
      throw BridgeError("Watchpoint " + std::to_string(id) + " not found", "not_found");
    return watch_json(watchpoint, "", nullptr);
  }
  SBBreakpoint breakpoint = target.FindBreakpointByID(static_cast<break_id_t>(id));
  if (!breakpoint.IsValid())
    throw BridgeError("Breakpoint " + std::to_string(id) + " not found", "not_found");
  return breakpoint_json(breakpoint, target);
}

Json breakpoint_delete(long long id, bool watch) {
  SBTarget target = require_target();
  bool deleted = watch ? target.DeleteWatchpoint(static_cast<watch_id_t>(id))
                       : target.BreakpointDelete(static_cast<break_id_t>(id));
  if (!deleted)
    throw BridgeError(std::string(watch ? "Watchpoint " : "Breakpoint ") + std::to_string(id) +
                          " not found",
                      "not_found");
  return Json::object_value();
}

Json breakpoint_enable(long long id, bool watch, bool enabled) {
  SBTarget target = require_target();
  if (watch) {
    SBWatchpoint watchpoint = target.FindWatchpointByID(static_cast<watch_id_t>(id));
    if (!watchpoint.IsValid())
      throw BridgeError("Watchpoint " + std::to_string(id) + " not found", "not_found");
    watchpoint.SetEnabled(enabled);
    return watch_json(watchpoint, "", nullptr);
  }
  SBBreakpoint breakpoint = target.FindBreakpointByID(static_cast<break_id_t>(id));
  if (!breakpoint.IsValid())
    throw BridgeError("Breakpoint " + std::to_string(id) + " not found", "not_found");
  breakpoint.SetEnabled(enabled);
  return breakpoint_json(breakpoint, target);
}

Json breakpoint_condition(long long id, bool watch, const std::string &condition) {
  SBTarget target = require_target();
  if (watch) {
    SBWatchpoint watchpoint = target.FindWatchpointByID(static_cast<watch_id_t>(id));
    if (!watchpoint.IsValid())
      throw BridgeError("Watchpoint " + std::to_string(id) + " not found", "not_found");
    watchpoint.SetCondition(condition.empty() ? nullptr : condition.c_str());
    return watch_json(watchpoint, "", nullptr);
  }
  SBBreakpoint breakpoint = target.FindBreakpointByID(static_cast<break_id_t>(id));
  if (!breakpoint.IsValid())
    throw BridgeError("Breakpoint " + std::to_string(id) + " not found", "not_found");
  breakpoint.SetCondition(condition.empty() ? nullptr : condition.c_str());
  return breakpoint_json(breakpoint, target);
}

// ---------------------------------------------------------------------------
// Inspection
// ---------------------------------------------------------------------------

bool parse_u64(const std::string &text, uint64_t &value) {
  if (text.empty())
    return false;
  errno = 0;
  char *end = nullptr;
  unsigned long long parsed = std::strtoull(text.c_str(), &end, 0);
  if (errno == ERANGE || end == text.c_str() || *end != '\0')
    return false;
  value = static_cast<uint64_t>(parsed);
  return true;
}

bool value_address(SBValue &value, uint64_t &address) {
  SBType type = value.GetType();
  bool pointer = type.IsValid() && type.IsPointerType();
  if (g_bridge->layer19.loaded() && g_bridge->layer19.api->value_address != nullptr &&
      pointer) {
    uint64_t fixed = 0;
    if (g_bridge->layer19.api->value_address(&value, &fixed) && fixed != 0) {
      address = fixed;
      return true;
    }
  }
  if (pointer) {
    uint64_t scalar = value.GetValueAsUnsigned(0);
    if (scalar != 0) {
      address = scalar;
      return true;
    }
  }
  addr_t load = value.GetLoadAddress();
  if (load != LLDB_INVALID_ADDRESS && load != 0) {
    address = static_cast<uint64_t>(load);
    return true;
  }
  uint64_t scalar = value.GetValueAsUnsigned(0);
  if (scalar != 0) {
    address = scalar;
    return true;
  }
  return false;
}

bool resolve_address(const std::string &expression, uint64_t &address, std::string &error) {
  std::string text = trim(expression);
  if (text.empty()) {
    error = "An address expression is required";
    return false;
  }
  if (parse_u64(text, address))
    return true;
  SBThread thread;
  SBFrame frame;
  ensure_thread(thread, frame, false);
  if (frame.IsValid()) {
    if (text == "$pc") {
      address = static_cast<uint64_t>(frame.GetPC());
      return address != 0;
    }
    if (text == "$sp") {
      address = static_cast<uint64_t>(frame.GetSP());
      return address != 0;
    }
    if (text == "$fp") {
      address = static_cast<uint64_t>(frame.GetFP());
      return address != 0;
    }
    SBValue value = frame.EvaluateExpression(text.c_str());
    if (value.IsValid() && !value.GetError().Fail() && value_address(value, address))
      return true;
  }
  SBTarget target = current_target();
  if (target.IsValid()) {
    SBSymbolContextList contexts = target.FindFunctions(text.c_str(), eFunctionNameTypeAny);
    if (contexts.GetSize() > 0) {
      SBSymbolContext context = contexts.GetContextAtIndex(0);
      SBAddress start = context.GetFunction().IsValid()
                             ? context.GetFunction().GetStartAddress()
                             : context.GetSymbol().GetStartAddress();
      if (start.IsValid()) {
        addr_t load = start.GetLoadAddress(target);
        if (load != LLDB_INVALID_ADDRESS && load != 0) {
          address = static_cast<uint64_t>(load);
          return true;
        }
      }
    }
  }
  error = "Could not resolve address expression: " + text;
  return false;
}

Json inspect_threads() {
  SBProcess process = current_process();
  Json threads = Json::array_value();
  Json current = Json::null();
  if (process.IsValid()) {
    SBThread selected;
    SBFrame frame;
    ensure_thread(selected, frame);
    for (uint32_t index = 0; index < process.GetNumThreads(); ++index) {
      SBThread thread = process.GetThreadAtIndex(index);
      if (thread.IsValid())
        threads.array.push_back(thread_json(thread, process));
    }
    if (selected.IsValid())
      current = Json::integer_value(selected.GetIndexID());
  }
  Json out = Json::object_value();
  out.object["threads"] = threads;
  out.object["current_thread_id"] = current;
  return out;
}

Json inspect_backtrace(const Json &request) {
  long long thread_id = as_integer(member(request, "thread_id"), -1);
  long long max_frames = as_integer(member(request, "max_frames"), 100);
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "no_stack_frame");
  SBThread thread = thread_id > 0 ? process.GetThreadByIndexID(static_cast<uint32_t>(thread_id))
                                  : process.GetSelectedThread();
  if (!thread.IsValid() && process.GetNumThreads() > 0)
    thread = process.GetThreadAtIndex(0);
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  Json frames = Json::array_value();
  for (uint32_t index = 0;
       index < thread.GetNumFrames() && static_cast<long long>(index) < max_frames; ++index) {
    SBFrame frame = thread.GetFrameAtIndex(index);
    if (frame.IsValid())
      frames.array.push_back(frame_json(frame, index));
  }
  Json out = Json::object_value();
  out.object["thread_id"] = Json::integer_value(thread.GetIndexID());
  out.object["frames"] = frames;
  return out;
}

Json inspect_frame(const Json &request) {
  long long thread_id = as_integer(member(request, "thread_id"), -1);
  long long frame_number = as_integer(member(request, "frame"), -1);
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "no_stack_frame");
  SBThread thread = thread_id > 0 ? process.GetThreadByIndexID(static_cast<uint32_t>(thread_id))
                                  : process.GetSelectedThread();
  if (!thread.IsValid() && process.GetNumThreads() > 0)
    thread = process.GetThreadAtIndex(0);
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  if (frame_number >= 0) {
    if (static_cast<uint32_t>(frame_number) >= thread.GetNumFrames())
      throw BridgeError("Frame " + std::to_string(frame_number) + " does not exist", "not_found");
    thread.SetSelectedFrame(static_cast<uint32_t>(frame_number));
  }
  SBFrame frame = thread.GetSelectedFrame();
  if (!frame.IsValid() && thread.GetNumFrames() > 0)
    frame = thread.GetFrameAtIndex(0);
  Json out = Json::object_value();
  out.object["frame"] = frame.IsValid() ? frame_json(frame, frame_index(thread)) : Json::null();
  return out;
}

Json select_thread(const Json &request) {
  long long thread_id = as_integer(member(request, "thread_id"), 0);
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "no_stack_frame");
  SBThread thread = process.GetThreadByIndexID(static_cast<uint32_t>(thread_id));
  if (!thread.IsValid())
    throw BridgeError("Thread " + std::to_string(thread_id) + " does not exist", "not_found");
  process.SetSelectedThread(thread);
  thread.SetSelectedFrame(0);
  SBFrame frame = thread.GetSelectedFrame();
  if (!frame.IsValid() && thread.GetNumFrames() > 0)
    frame = thread.GetFrameAtIndex(0);
  Json out = Json::object_value();
  out.object["thread_id"] = Json::integer_value(thread.GetIndexID());
  out.object["frame"] = frame.IsValid() ? frame_json(frame, 0) : Json::null();
  return out;
}

Json select_frame(const Json &request) {
  long long frame_number = as_integer(member(request, "frame"), 0);
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "no_stack_frame");
  SBThread thread = process.GetSelectedThread();
  if (!thread.IsValid() && process.GetNumThreads() > 0) {
    thread = process.GetThreadAtIndex(0);
    process.SetSelectedThread(thread);
  }
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  if (frame_number < 0 || static_cast<uint32_t>(frame_number) >= thread.GetNumFrames())
    throw BridgeError("Frame " + std::to_string(frame_number) + " does not exist", "not_found");
  thread.SetSelectedFrame(static_cast<uint32_t>(frame_number));
  SBFrame frame = thread.GetSelectedFrame();
  Json out = Json::object_value();
  out.object["frame_number"] = Json::integer_value(frame_number);
  out.object["frame"] = frame.IsValid() ? frame_json(frame, static_cast<uint32_t>(frame_number))
                                        : Json::null();
  return out;
}

Json evaluate(const Json &request) {
  std::string expression = as_string(member(request, "expression"));
  long long thread_id = as_integer(member(request, "thread_id"), -1);
  long long frame_number = as_integer(member(request, "frame"), -1);
  long long timeout_ms = as_integer(member(request, "timeout_ms"), 30000);
  SBThread thread;
  SBFrame frame;
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "not_started");
  thread = thread_id > 0 ? process.GetThreadByIndexID(static_cast<uint32_t>(thread_id))
                         : process.GetSelectedThread();
  if (!thread.IsValid() && process.GetNumThreads() > 0)
    thread = process.GetThreadAtIndex(0);
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  if (frame_number >= 0)
    thread.SetSelectedFrame(static_cast<uint32_t>(frame_number));
  frame = thread.GetSelectedFrame();
  if (!frame.IsValid() && thread.GetNumFrames() > 0)
    frame = thread.GetFrameAtIndex(0);
  SBExpressionOptions options;
  options.SetIgnoreBreakpoints(true);
  options.SetUnwindOnError(true);
  options.SetTryAllThreads(false);
  if (timeout_ms > 0)
    options.SetTimeoutInMicroSeconds(static_cast<uint32_t>(timeout_ms * 1000));
  SBValue value = frame.IsValid() ? frame.EvaluateExpression(expression.c_str(), options)
                                  : require_target().EvaluateExpression(expression.c_str(), options);
  SBError error = value.GetError();
  if (!value.IsValid() || error.Fail())
    throw BridgeError(error.Fail() ? S(error.GetCString()) : "expression did not produce a value");
  Json out = Json::object_value();
  out.object["expression"] = Json::string_value(expression);
  out.object["value"] = Json::string_value(value_json(value).object["value"].string);
  out.object["type"] = Json::string_value(S(value.GetTypeName()));
  out.object["summary"] = Json::string_value(S(value.GetSummary()));
  return out;
}

Json variables(const Json &request) {
  long long thread_id = as_integer(member(request, "thread_id"), -1);
  long long frame_number = as_integer(member(request, "frame"), 0);
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "not_started");
  SBThread thread = thread_id > 0 ? process.GetThreadByIndexID(static_cast<uint32_t>(thread_id))
                                  : process.GetSelectedThread();
  if (!thread.IsValid() && process.GetNumThreads() > 0)
    thread = process.GetThreadAtIndex(0);
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  if (frame_number >= 0 && static_cast<uint32_t>(frame_number) < thread.GetNumFrames())
    thread.SetSelectedFrame(static_cast<uint32_t>(frame_number));
  SBFrame frame = thread.GetSelectedFrame();
  if (!frame.IsValid() && thread.GetNumFrames() > 0)
    frame = thread.GetFrameAtIndex(0);
  SBVariablesOptions options;
  options.SetIncludeArguments(true);
  options.SetIncludeLocals(true);
  options.SetIncludeStatics(false);
  options.SetInScopeOnly(true);
  options.SetIncludeRuntimeSupportValues(false);
  if (g_bridge->layer8.loaded() && g_bridge->layer8.api->set_include_recognized_arguments != nullptr)
    g_bridge->layer8.api->set_include_recognized_arguments(&options, 1);
  SBValueList values = frame.GetVariables(options);
  Json items = Json::array_value();
  for (uint32_t index = 0; index < values.GetSize(); ++index) {
    SBValue value = values.GetValueAtIndex(index);
    if (!value.IsValid() || S(value.GetName()).empty())
      continue;
    items.array.push_back(value_json(value));
  }
  Json out = Json::object_value();
  out.object["thread_id"] = Json::integer_value(thread.GetIndexID());
  out.object["frame"] = Json::integer_value(frame_number);
  out.object["variables"] = items;
  return out;
}

Json registers(const Json &request) {
  long long thread_id = as_integer(member(request, "thread_id"), -1);
  long long frame_number = as_integer(member(request, "frame"), -1);
  long long max_registers = as_integer(member(request, "max_registers"), -1);
  std::string format = as_string(member(request, "value_format"), "hex");
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "not_started");
  SBThread thread = thread_id > 0 ? process.GetThreadByIndexID(static_cast<uint32_t>(thread_id))
                                  : process.GetSelectedThread();
  if (!thread.IsValid() && process.GetNumThreads() > 0)
    thread = process.GetThreadAtIndex(0);
  if (!thread.IsValid())
    throw BridgeError("No thread is selected", "no_stack_frame");
  if (frame_number >= 0 && static_cast<uint32_t>(frame_number) < thread.GetNumFrames())
    thread.SetSelectedFrame(static_cast<uint32_t>(frame_number));
  SBFrame frame = thread.GetSelectedFrame();
  if (!frame.IsValid() && thread.GetNumFrames() > 0)
    frame = thread.GetFrameAtIndex(0);
  SBValueList sets = frame.GetRegisters();
  Json items = Json::array_value();
  long long number = 0;
  for (uint32_t set_index = 0; set_index < sets.GetSize(); ++set_index) {
    SBValue set = sets.GetValueAtIndex(set_index);
    if (!set.IsValid())
      continue;
    if (set.GetNumChildren() == 0) {
      Json item = Json::object_value();
      item.object["number"] = Json::integer_value(number++);
      item.object["name"] = Json::string_value(S(set.GetName()));
      item.object["value"] = Json::string_value(S(set.GetValue()));
      items.array.push_back(item);
    } else {
      for (uint32_t child_index = 0; child_index < set.GetNumChildren(); ++child_index) {
        SBValue child = set.GetChildAtIndex(child_index);
        if (!child.IsValid())
          continue;
        std::string text = S(child.GetValue());
        if (format == "hex" && !text.empty() && text.rfind("0x", 0) != 0) {
          std::string type = S(child.GetTypeName());
          if (type.find("int") != std::string::npos ||
              type.find("long") != std::string::npos ||
              type.find("short") != std::string::npos ||
              type.find("char") != std::string::npos || type.find("*") != std::string::npos)
            text = hex_address(child.GetValueAsUnsigned(0));
        }
        Json item = Json::object_value();
        item.object["number"] = Json::integer_value(number++);
        item.object["name"] = Json::string_value(S(child.GetName()));
        item.object["value"] = Json::string_value(text);
        items.array.push_back(item);
      }
    }
  }
  if (max_registers >= 0 && static_cast<long long>(items.array.size()) > max_registers)
    items.array.resize(static_cast<size_t>(max_registers));
  Json out = Json::object_value();
  out.object["registers"] = items;
  return out;
}

Json read_memory(const Json &request) {
  std::string expression = as_string(member(request, "address"));
  long long count = as_integer(member(request, "count"), 0);
  long long offset = as_integer(member(request, "offset"), 0);
  if (count <= 0)
    throw BridgeError("count must be positive", "validation_error");
  uint64_t address = 0;
  std::string error;
  if (!resolve_address(expression, address, error))
    throw BridgeError(error, "memory_unavailable");
  address = static_cast<uint64_t>(static_cast<int64_t>(address) + offset);
  SBProcess process = current_process();
  if (!process.IsValid())
    throw BridgeError("The program has not started", "memory_unavailable");
  std::vector<unsigned char> buffer(static_cast<size_t>(count));
  SBError read_error;
  size_t read = process.ReadMemory(static_cast<addr_t>(address), buffer.data(),
                                   buffer.size(), read_error);
  if (read == 0)
    throw BridgeError(read_error.Fail() ? S(read_error.GetCString())
                                        : "no readable memory at " + hex_address(address),
                      "memory_unavailable");
  static const char *digits = "0123456789abcdef";
  std::string data;
  for (size_t index = 0; index < read; ++index) {
    data += digits[(buffer[index] >> 4) & 0xF];
    data += digits[buffer[index] & 0xF];
  }
  Json out = Json::object_value();
  out.object["address"] = Json::string_value(hex_address(address));
  out.object["data"] = Json::string_value(data);
  out.object["read"] = Json::integer_value(static_cast<long long>(read));
  out.object["count"] = Json::integer_value(count);
  out.object["offset"] = Json::integer_value(offset);
  return out;
}

Json disassemble(const Json &request) {
  SBTarget target = require_target();
  std::string function = as_string(member(request, "function"));
  std::string address_text = as_string(member(request, "address"));
  std::string start_text = as_string(member(request, "start_address"));
  std::string end_text = as_string(member(request, "end_address"));
  std::string file = as_string(member(request, "file"));
  long long line = as_integer(member(request, "line"), 0);
  long long instruction_count = as_integer(member(request, "instruction_count"), 32);
  std::string mode = as_string(member(request, "mode"), "mixed");
  bool mixed = mode == "mixed";
  if (instruction_count <= 0)
    instruction_count = 1;
  SBAddress start;
  std::string resolved_function = function;
  if (!function.empty()) {
    SBSymbolContextList contexts = target.FindFunctions(function.c_str(), eFunctionNameTypeAny);
    if (contexts.GetSize() == 0)
      throw BridgeError("Function not found: " + function, "not_found");
    SBSymbolContext context = contexts.GetContextAtIndex(0);
    if (context.GetFunction().IsValid()) {
      resolved_function = S(context.GetFunction().GetName());
      start = context.GetFunction().GetStartAddress();
    } else {
      resolved_function = S(context.GetSymbol().GetName());
      start = context.GetSymbol().GetStartAddress();
    }
  } else if (!start_text.empty() || !address_text.empty()) {
    uint64_t value = 0;
    std::string error;
    if (!resolve_address(start_text.empty() ? address_text : start_text, value, error))
      throw BridgeError(error, "disassembly_unavailable");
    start = target.ResolveLoadAddress(static_cast<addr_t>(value));
  } else if (!file.empty() && line > 0) {
    SBBreakpoint bp = target.BreakpointCreateByLocation(file.c_str(), static_cast<uint32_t>(line));
    if (!bp.IsValid() || bp.GetNumLocations() == 0) {
      if (bp.IsValid())
        target.BreakpointDelete(bp.GetID());
      throw BridgeError("No code found at " + file + ":" + std::to_string(line), "not_found");
    }
    start = bp.GetLocationAtIndex(0).GetAddress();
    target.BreakpointDelete(bp.GetID());
  } else {
    SBThread thread;
    SBFrame frame;
    ensure_thread(thread, frame, false);
    if (!frame.IsValid())
      throw BridgeError("No frame is selected", "no_stack_frame");
    start = frame.GetPCAddress();
    if (resolved_function.empty())
      resolved_function = S(frame.GetFunctionName());
  }
  if (!start.IsValid())
    throw BridgeError("Could not resolve a disassembly address", "disassembly_unavailable");
  uint64_t end = 0;
  bool has_end = false;
  if (!end_text.empty()) {
    std::string error;
    if (!resolve_address(end_text, end, error))
      throw BridgeError(error, "disassembly_unavailable");
    has_end = true;
  }
  uint64_t current_pc = 0;
  SBThread current_thread;
  SBFrame current_frame;
  ensure_thread(current_thread, current_frame, false);
  if (current_frame.IsValid())
    current_pc = static_cast<uint64_t>(current_frame.GetPC());
  uint64_t limit = has_end ? 4096 : static_cast<uint64_t>(instruction_count);
  SBInstructionList instructions = target.ReadInstructions(start, static_cast<uint32_t>(limit));
  Json items = Json::array_value();
  uint64_t first_address = 0;
  uint64_t last_address = 0;
  size_t last_size = 0;
  for (size_t index = 0; index < instructions.GetSize(); ++index) {
    SBInstruction instruction = instructions.GetInstructionAtIndex(static_cast<uint32_t>(index));
    if (!instruction.IsValid())
      continue;
    SBAddress address = instruction.GetAddress();
    addr_t load = address.GetLoadAddress(target);
    uint64_t numeric = load == LLDB_INVALID_ADDRESS ? address.GetFileAddress() : load;
    std::string mnemonic = S(instruction.GetMnemonic(target));
    std::string operands = S(instruction.GetOperands(target));
    std::string text = mnemonic;
    if (!operands.empty()) {
      if (!text.empty())
        text += ' ';
      text += operands;
    }
    Json item = Json::object_value();
    item.object["address"] = Json::string_value(hex_address(numeric));
    item.object["instruction"] = Json::string_value(text);
    item.object["offset"] = Json::integer_value(0);
    std::string name = S(address.GetFunction().GetName());
    if (!name.empty())
      item.object["function"] = Json::string_value(name);
    if (mixed) {
      SBLineEntry line_entry = address.GetLineEntry();
      std::string path = file_path(line_entry.GetFileSpec());
      if (!path.empty()) {
        item.object["file"] = Json::string_value(path);
        item.object["fullname"] = Json::string_value(path);
      }
      if (line_entry.GetLine() != 0 && line_entry.GetLine() != UINT32_MAX)
        item.object["line"] = Json::integer_value(line_entry.GetLine());
    }
    item.object["is_current"] = Json::boolean_value(numeric == current_pc);
    SBData data = instruction.GetData(target);
    if (data.GetByteSize() > 0) {
      std::vector<unsigned char> bytes(data.GetByteSize());
      SBError data_error;
      if (data.ReadRawData(data_error, 0, bytes.data(), bytes.size()) == bytes.size()) {
        static const char *digits = "0123456789abcdef";
        std::string opcodes;
        for (unsigned char byte : bytes) {
          opcodes += digits[(byte >> 4) & 0xF];
          opcodes += digits[byte & 0xF];
        }
        item.object["opcodes"] = Json::string_value(opcodes);
      }
    }
    items.array.push_back(item);
    if (first_address == 0)
      first_address = numeric;
    last_address = numeric;
    last_size = instruction.GetByteSize();
    if (has_end && numeric >= end)
      break;
    if (!has_end && static_cast<long long>(items.array.size()) >= instruction_count)
      break;
  }
  if (items.array.empty())
    throw BridgeError("LLDB returned no disassembly", "disassembly_unavailable");
  Json out = Json::object_value();
  out.object["instructions"] = items;
  out.object["function"] =
      resolved_function.empty() ? Json::null() : Json::string_value(resolved_function);
  out.object["start_address"] = Json::string_value(hex_address(first_address));
  if (last_address)
    out.object["end_address"] = Json::string_value(hex_address(last_address + last_size));
  return out;
}

Json source_context(const Json &request) {
  SBTarget target = require_target();
  std::string function = as_string(member(request, "function"));
  std::string address_text = as_string(member(request, "address"));
  std::string file = as_string(member(request, "file"));
  long long line = as_integer(member(request, "line"), 0);
  long long start_arg = as_integer(member(request, "start_line"), 0);
  long long end_arg = as_integer(member(request, "end_line"), 0);
  long long before = as_integer(member(request, "context_before"), 5);
  long long after = as_integer(member(request, "context_after"), 5);
  SBLineEntry location;
  std::string resolved_function = function;
  if (!function.empty()) {
    SBSymbolContextList contexts = target.FindFunctions(function.c_str(), eFunctionNameTypeAny);
    if (contexts.GetSize() == 0)
      throw BridgeError("Function not found: " + function, "not_found");
    SBSymbolContext context = contexts.GetContextAtIndex(0);
    SBAddress start = context.GetFunction().IsValid()
                           ? context.GetFunction().GetStartAddress()
                           : context.GetSymbol().GetStartAddress();
    location = start.GetLineEntry();
    resolved_function = context.GetFunction().IsValid()
                            ? S(context.GetFunction().GetName())
                            : S(context.GetSymbol().GetName());
  } else if (!address_text.empty()) {
    uint64_t value = 0;
    std::string error;
    if (!resolve_address(address_text, value, error))
      throw BridgeError(error, "not_found");
    location = target.ResolveLoadAddress(static_cast<addr_t>(value)).GetLineEntry();
  } else {
    SBThread thread;
    SBFrame frame;
    ensure_thread(thread, frame, false);
    if (!frame.IsValid())
      throw BridgeError("No frame is selected", "no_stack_frame");
    location = frame.GetLineEntry();
    if (resolved_function.empty())
      resolved_function = S(frame.GetFunctionName());
  }
  if (file.empty())
    file = file_path(location.GetFileSpec());
  if (line <= 0 && location.GetLine() != 0 && location.GetLine() != UINT32_MAX)
    line = location.GetLine();
  if (file.empty() || line <= 0)
    throw BridgeError("LLDB did not report source for this location", "not_found");
  long long start_line = start_arg > 0 ? start_arg : std::max<long long>(1, line - before);
  long long end_line = end_arg > 0 ? end_arg : line + after;
  if (start_line > end_line)
    std::swap(start_line, end_line);
  std::ifstream stream(file);
  if (!stream)
    throw BridgeError("Could not read source file: " + file, "not_found");
  std::vector<std::string> lines;
  std::string current;
  while (std::getline(stream, current)) {
    if (!current.empty() && current.back() == '\r')
      current.pop_back();
    lines.push_back(current);
  }
  Json items = Json::array_value();
  for (long long number = start_line;
       number <= end_line && number <= static_cast<long long>(lines.size()); ++number) {
    if (number < 1)
      continue;
    Json item = Json::object_value();
    item.object["line_number"] = Json::integer_value(number);
    item.object["text"] = Json::string_value(lines[static_cast<size_t>(number - 1)]);
    if (number == line && location.GetColumn() != 0 && location.GetColumn() != UINT32_MAX)
      item.object["column"] = Json::integer_value(location.GetColumn());
    item.object["is_current"] = Json::boolean_value(number == line);
    items.array.push_back(item);
  }
  if (items.array.empty())
    throw BridgeError("No source lines in the requested window", "not_found");
  Json out = Json::object_value();
  out.object["file"] = Json::string_value(file);
  out.object["fullname"] = Json::string_value(file);
  out.object["function"] =
      resolved_function.empty() ? Json::null() : Json::string_value(resolved_function);
  out.object["line"] = Json::integer_value(line);
  out.object["start_line"] = Json::integer_value(start_line);
  out.object["end_line"] = Json::integer_value(
      std::min<long long>(end_line, static_cast<long long>(lines.size())));
  out.object["lines"] = items;
  return out;
}

Json command(const Json &request) {
  std::string text = as_string(member(request, "command"));
  if (trim(text).empty())
    throw BridgeError("A command is required", "validation_error");
  SBCommandInterpreter interpreter = g_bridge->debugger.GetCommandInterpreter();
  SBCommandReturnObject result;
  interpreter.HandleCommand(text.c_str(), result, false);
  Json out = Json::object_value();
  out.object["output"] = Json::string_value(S(result.GetOutput()));
  out.object["error"] = Json::string_value(S(result.GetError()));
  out.object["succeeded"] = Json::boolean_value(result.Succeeded());
  return out;
}

Json target_list() {
  Json items = Json::array_value();
  for (uint32_t index = 0; index < g_bridge->debugger.GetNumTargets(); ++index) {
    SBTarget target = g_bridge->debugger.GetTargetAtIndex(index);
    if (target.IsValid())
      items.array.push_back(target_json(target, static_cast<int>(index)));
  }
  Json out = Json::object_value();
  out.object["targets"] = items;
  out.object["current_inferior_id"] = Json::integer_value(g_bridge->selected_index + 1);
  return out;
}

Json target_create(const Json &request) {
  std::string executable = as_string(member(request, "executable"));
  bool make_current = as_bool(member(request, "make_current"), false);
  SBTarget target = create_target(executable, make_current);
  int index = make_current ? g_bridge->selected_index
                           : static_cast<int>(g_bridge->debugger.GetNumTargets()) - 1;
  Json out = target_json(target, index);
  out.object["current_inferior_id"] = Json::integer_value(g_bridge->selected_index + 1);
  return out;
}

Json target_select(const Json &request) {
  long long id = as_integer(member(request, "inferior_id"), 0);
  if (id <= 0)
    throw BridgeError("A positive inferior_id is required", "validation_error");
  select_target_index(static_cast<int>(id - 1));
  SBTarget target = current_target();
  return target_json(target, g_bridge->selected_index);
}

Json target_delete(const Json &request) {
  long long id = as_integer(member(request, "inferior_id"), 0);
  if (id <= 0)
    throw BridgeError("A positive inferior_id is required", "validation_error");
  int index = static_cast<int>(id - 1);
  if (index < 0 || index >= static_cast<int>(g_bridge->debugger.GetNumTargets()))
    throw BridgeError("Inferior " + std::to_string(id) + " not found", "not_found");
  SBTarget target = g_bridge->debugger.GetTargetAtIndex(static_cast<uint32_t>(index));
  if (!target.IsValid())
    throw BridgeError("Inferior " + std::to_string(id) + " not found", "not_found");
  SBProcess process = target.GetProcess();
  if (process.IsValid())
    process.Kill();
  g_bridge->debugger.DeleteTarget(target);
  if (g_bridge->debugger.GetNumTargets() == 0) {
    g_bridge->selected_index = 0;
    Json out = Json::object_value();
    out.object["current_inferior_id"] = Json::integer_value(0);
    return out;
  }
  if (g_bridge->selected_index >= static_cast<int>(g_bridge->debugger.GetNumTargets()))
    g_bridge->selected_index = static_cast<int>(g_bridge->debugger.GetNumTargets()) - 1;
  if (g_bridge->selected_index < 0)
    g_bridge->selected_index = 0;
  select_target_index(g_bridge->selected_index);
  Json out = Json::object_value();
  out.object["current_inferior_id"] = Json::integer_value(g_bridge->selected_index + 1);
  return out;
}

// ---------------------------------------------------------------------------
// Dispatch and lifecycle
// ---------------------------------------------------------------------------

Json initialize() {
  if (g_bridge->initialized)
    return Json::object_value();
  SBDebugger::Initialize();
  g_bridge->debugger = SBDebugger::Create(false);
  if (!g_bridge->debugger.IsValid())
    throw BridgeError("Could not create an LLDB debugger");
  g_bridge->debugger.SetAsync(true);
  g_bridge->debugger.SetUseColor(false);
  g_bridge->listener = g_bridge->debugger.GetListener();
  g_bridge->initialized = true;
  load_layers();
  Json out = Json::object_value();
  out.object["version"] = Json::string_value(S(SBDebugger::GetVersionString()));
  out.object["instance"] = Json::string_value(S(g_bridge->debugger.GetInstanceName()));
  Json features = Json::array_value();
  if (g_bridge->layer7.loaded())
    features.array.push_back(Json::string_value("error_overloads"));
  if (g_bridge->layer8.loaded())
    features.array.push_back(Json::string_value("recognized_args"));
  if (g_bridge->layer16.loaded())
    features.array.push_back(Json::string_value("siginfo"));
  if (g_bridge->layer18.loaded())
    features.array.push_back(Json::string_value("watchpoint_introspection"));
  if (g_bridge->layer19.loaded())
    features.array.push_back(Json::string_value("address_fixup"));
  out.object["features"] = features;
  return out;
}

Json shutdown(const Json &request) {
  bool detach = as_bool(member(request, "detach"), false);
  SBProcess process = current_process();
  if (process.IsValid()) {
    if (detach)
      process.Detach();
    else
      process.Kill();
  }
  if (g_bridge->initialized) {
    while (g_bridge->debugger.GetNumTargets() > 0) {
      SBTarget target = g_bridge->debugger.GetTargetAtIndex(0);
      if (!target.IsValid())
        break;
      g_bridge->debugger.DeleteTarget(target);
    }
    SBDebugger::Destroy(g_bridge->debugger);
    SBDebugger::Terminate();
    g_bridge->initialized = false;
  }
  for (Layer *layer : {&g_bridge->layer7, &g_bridge->layer8, &g_bridge->layer16,
                       &g_bridge->layer18, &g_bridge->layer19}) {
    close_library(layer->handle);
    layer->handle = nullptr;
    layer->api = nullptr;
  }
  return Json::object_value();
}

Json dispatch(const std::string &operation, const Json &request) {
  if (operation == "initialize")
    return initialize();
  if (operation == "shutdown")
    return shutdown(request);
  if (operation == "configure_launch")
    return configure_launch(request);
  if (operation == "create_target") {
    SBTarget target = create_target(as_string(member(request, "program")), true);
    return target_json(target, g_bridge->selected_index);
  }
  if (operation == "launch")
    return launch(request);
  if (operation == "attach")
    return attach_process(request);
  if (operation == "connect_remote")
    return connect_remote(request);
  if (operation == "load_core")
    return load_core(request);
  if (operation == "status")
    return status_json();
  if (operation == "wait") {
    long long timeout_ms = as_integer(member(request, "timeout_ms"), 30000);
    return wait_for_terminal(std::max<long long>(timeout_ms, 0));
  }
  if (operation == "continue") {
    SBProcess process = current_process();
    if (!process.IsValid())
      throw BridgeError("The program has not started; use run", "not_started");
    if (process.GetState() == eStateExited || process.GetState() == eStateDetached)
      throw BridgeError("The program has exited", "not_started");
    if (process.GetState() == eStateRunning || process.GetState() == eStateStepping)
      throw BridgeError("The program is already running", "not_started");
    SBError error = process.Continue();
    if (error.Fail())
      throw BridgeError(S(error.GetCString()));
    return status_json();
  }
  if (operation == "step")
    return step_thread(as_string(member(request, "kind"), "into"));
  if (operation == "return_from_frame")
    return return_from_frame(request);
  if (operation == "execute_location")
    return execute_location(request);
  if (operation == "interrupt") {
    SBProcess process = current_process();
    if (!process.IsValid())
      throw BridgeError("The program has not started", "not_started");
    bool requested = false;
    if (process.GetState() == eStateRunning || process.GetState() == eStateStepping) {
      SBError error = process.Stop();
      if (error.Fail())
        throw BridgeError(S(error.GetCString()));
      requested = true;
    }
    long long timeout_ms = as_integer(member(request, "timeout_ms"), 5000);
    return wait_for_terminal(timeout_ms, requested ? "interrupt" : nullptr);
  }
  if (operation == "threads")
    return inspect_threads();
  if (operation == "backtrace")
    return inspect_backtrace(request);
  if (operation == "frame")
    return inspect_frame(request);
  if (operation == "select_thread")
    return select_thread(request);
  if (operation == "select_frame")
    return select_frame(request);
  if (operation == "evaluate")
    return evaluate(request);
  if (operation == "variables")
    return variables(request);
  if (operation == "registers")
    return registers(request);
  if (operation == "memory")
    return read_memory(request);
  if (operation == "disassemble")
    return disassemble(request);
  if (operation == "source")
    return source_context(request);
  if (operation == "breakpoint_create")
    return breakpoint_create(request);
  if (operation == "breakpoint_list")
    return breakpoint_list();
  if (operation == "breakpoint_get")
    return breakpoint_find(as_integer(member(request, "id"), 0), false);
  if (operation == "breakpoint_delete") {
    breakpoint_delete(as_integer(member(request, "id"), 0), false);
    return Json::object_value();
  }
  if (operation == "breakpoint_enable")
    return breakpoint_enable(as_integer(member(request, "id"), 0), false,
                             as_bool(member(request, "enabled"), true));
  if (operation == "breakpoint_condition")
    return breakpoint_condition(as_integer(member(request, "id"), 0), false,
                                as_string(member(request, "condition")));
  if (operation == "watchpoint_create")
    return watchpoint_create(request);
  if (operation == "watchpoint_list")
    return watchpoint_list();
  if (operation == "watchpoint_get")
    return breakpoint_find(as_integer(member(request, "id"), 0), true);
  if (operation == "watchpoint_delete") {
    breakpoint_delete(as_integer(member(request, "id"), 0), true);
    return Json::object_value();
  }
  if (operation == "watchpoint_enable")
    return breakpoint_enable(as_integer(member(request, "id"), 0), true,
                             as_bool(member(request, "enabled"), true));
  if (operation == "watchpoint_condition")
    return breakpoint_condition(as_integer(member(request, "id"), 0), true,
                                as_string(member(request, "condition")));
  if (operation == "command")
    return command(request);
  if (operation == "setting_set")
    return setting_set(request);
  if (operation == "setting_get")
    return setting_get(request);
  if (operation == "target_list")
    return target_list();
  if (operation == "target_create")
    return target_create(request);
  if (operation == "target_select")
    return target_select(request);
  if (operation == "target_delete")
    return target_delete(request);
  throw BridgeError("Unknown bridge operation: " + operation, "unknown_operation");
}

void write_response(int fd, long long id, bool ok, const Json &payload, const std::string &error,
                    const std::string &code) {
  Json response = Json::object_value();
  response.object["request_id"] = Json::integer_value(id);
  response.object["ok"] = Json::boolean_value(ok);
  if (ok) {
    for (const auto &item : payload.object)
      response.object[item.first] = item.second;
  } else {
    response.object["error"] = Json::string_value(error);
    response.object["code"] = Json::string_value(code.empty() ? "error" : code);
  }
  std::string text = dump_string(response) + "\n";
  const char *cursor = text.data();
  size_t remaining = text.size();
  while (remaining > 0) {
    long written = bridge_write(fd, cursor, remaining);
    if (written <= 0) {
      if (errno == EINTR)
        continue;
      break;
    }
    cursor += written;
    remaining -= static_cast<size_t>(written);
  }
}

int run_bridge(int protocol_fd) {
  std::string line;
  while (std::getline(std::cin, line)) {
    if (trim(line).empty())
      continue;
    long long id = 0;
    try {
      Json request = JsonParser(line).parse();
      id = as_integer(member(request, "request_id"), 0);
      std::string operation = as_string(member(request, "op"));
      Json payload = dispatch(operation, request);
      write_response(protocol_fd, id, true, payload, "", "");
      if (operation == "shutdown")
        break;
    } catch (const BridgeError &error) {
      write_response(protocol_fd, id, false, Json::object_value(), error.what(), error.code);
    } catch (const std::exception &error) {
      write_response(protocol_fd, id, false, Json::object_value(), error.what(), "error");
    }
  }
  if (g_bridge != nullptr) {
    if (g_bridge->initialized) {
      try {
        shutdown(Json::object_value());
      } catch (...) {
      }
    }
    delete g_bridge;
    g_bridge = nullptr;
  }
  return 0;
}

} // namespace

int main() {
  int protocol_fd = bridge_dup_stdout();
  if (protocol_fd < 0)
    return 1;
  int devnull = bridge_open_null();
  if (devnull >= 0)
    bridge_replace_stdout(devnull);
  std::ios::sync_with_stdio(false);
  g_bridge = new Bridge();
  int result = run_bridge(protocol_fd);
  bridge_close(protocol_fd);
  return result;
}
