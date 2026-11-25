"""ctypes bindings for librime 1.17.x (32-bit Windows / PIME backend).

These definitions follow ``rime_api.h`` from librime 1.17.0.  Nothing here
runs at import time: callers explicitly invoke :func:`load_rime_dll` with the
path to ``rime.dll`` so the package can be imported and unit-tested cleanly on
any machine.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import (
    CDLL,
    CFUNCTYPE,
    POINTER,
    Structure,
    c_char_p,
    c_double,
    c_int,
    c_size_t,
    c_void_p,
)

Bool = c_int
RimeSessionId = c_size_t
RimeNotificationHandler = CFUNCTYPE(None, c_void_p, RimeSessionId, c_char_p, c_char_p)


class _RimeStruct(Structure):
    """Base for self-versioned librime C structs carrying ``data_size``."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_size = ctypes.sizeof(self) - ctypes.sizeof(c_int)


class RimeTraits(_RimeStruct):
    _fields_ = [
        ("data_size", c_int),
        ("shared_data_dir", c_char_p),
        ("user_data_dir", c_char_p),
        ("distribution_name", c_char_p),
        ("distribution_code_name", c_char_p),
        ("distribution_version", c_char_p),
        ("app_name", c_char_p),
        ("modules", POINTER(c_char_p)),
        ("min_log_level", c_int),
        ("log_dir", c_char_p),
        ("prebuilt_data_dir", c_char_p),
        ("staging_dir", c_char_p),
    ]


class RimeComposition(Structure):
    _fields_ = [
        ("length", c_int),
        ("cursor_pos", c_int),
        ("sel_start", c_int),
        ("sel_end", c_int),
        ("preedit", c_char_p),
    ]


class RimeCandidate(Structure):
    _fields_ = [
        ("text", c_char_p),
        ("comment", c_char_p),
        ("reserved", c_void_p),
    ]


class RimeMenu(Structure):
    _fields_ = [
        ("page_size", c_int),
        ("page_no", c_int),
        ("is_last_page", Bool),
        ("highlighted_candidate_index", c_int),
        ("num_candidates", c_int),
        ("candidates", POINTER(RimeCandidate)),
        ("select_keys", c_char_p),
    ]


class RimeCommit(_RimeStruct):
    _fields_ = [
        ("data_size", c_int),
        ("text", c_char_p),
    ]


class RimeContext(_RimeStruct):
    _fields_ = [
        ("data_size", c_int),
        ("composition", RimeComposition),
        ("menu", RimeMenu),
        ("commit_text_preview", c_char_p),
        ("select_labels", POINTER(c_char_p)),
    ]


class RimeStatus(_RimeStruct):
    _fields_ = [
        ("data_size", c_int),
        ("schema_id", c_char_p),
        ("schema_name", c_char_p),
        ("is_disabled", Bool),
        ("is_composing", Bool),
        ("is_ascii_mode", Bool),
        ("is_full_shape", Bool),
        ("is_simplified", Bool),
        ("is_traditional", Bool),
        ("is_ascii_punct", Bool),
    ]


class RimeCandidateListIterator(Structure):
    _fields_ = [
        ("ptr", c_void_p),
        ("index", c_int),
        ("candidate", RimeCandidate),
    ]


class RimeConfig(Structure):
    _fields_ = [("ptr", c_void_p)]


class RimeConfigIterator(Structure):
    _fields_ = [
        ("list", c_void_p),
        ("map", c_void_p),
        ("index", c_int),
        ("key", c_char_p),
        ("path", c_char_p),
    ]


class RimeSchemaListItem(Structure):
    _fields_ = [
        ("schema_id", c_char_p),
        ("name", c_char_p),
        ("reserved", c_void_p),
    ]


class RimeSchemaList(Structure):
    _fields_ = [
        ("size", c_size_t),
        ("list", POINTER(RimeSchemaListItem)),
    ]


class RimeStringSlice(Structure):
    _fields_ = [
        ("str", c_char_p),
        ("length", c_size_t),
    ]


class RimeCustomApi(_RimeStruct):
    _fields_ = [("data_size", c_int)]


class RimeModule(_RimeStruct):
    _fields_ = [
        ("data_size", c_int),
        ("module_name", c_char_p),
        ("initialize", c_void_p),
        ("finalize", c_void_p),
        ("get_api", CFUNCTYPE(POINTER(RimeCustomApi))),
    ]


class RimeApi(_RimeStruct):
    _fields_ = [
        ("data_size", c_int),
        ("setup", CFUNCTYPE(None, POINTER(RimeTraits))),
        ("set_notification_handler", CFUNCTYPE(None, RimeNotificationHandler, c_void_p)),
        ("initialize", CFUNCTYPE(None, POINTER(RimeTraits))),
        ("finalize", CFUNCTYPE(None)),
        ("start_maintenance", CFUNCTYPE(Bool, Bool)),
        ("is_maintenance_mode", CFUNCTYPE(Bool)),
        ("join_maintenance_thread", CFUNCTYPE(None)),
        ("deployer_initialize", CFUNCTYPE(None, POINTER(RimeTraits))),
        ("prebuild", CFUNCTYPE(Bool)),
        ("deploy", CFUNCTYPE(Bool)),
        ("deploy_schema", CFUNCTYPE(Bool, c_char_p)),
        ("deploy_config_file", CFUNCTYPE(Bool, c_char_p, c_char_p)),
        ("sync_user_data", CFUNCTYPE(Bool)),
        ("create_session", CFUNCTYPE(RimeSessionId)),
        ("find_session", CFUNCTYPE(Bool, RimeSessionId)),
        ("destroy_session", CFUNCTYPE(Bool, RimeSessionId)),
        ("cleanup_stale_sessions", CFUNCTYPE(None)),
        ("cleanup_all_sessions", CFUNCTYPE(None)),
        ("process_key", CFUNCTYPE(Bool, RimeSessionId, c_int, c_int)),
        ("commit_composition", CFUNCTYPE(Bool, RimeSessionId)),
        ("clear_composition", CFUNCTYPE(None, RimeSessionId)),
        ("get_commit", CFUNCTYPE(Bool, RimeSessionId, POINTER(RimeCommit))),
        ("free_commit", CFUNCTYPE(Bool, POINTER(RimeCommit))),
        ("get_context", CFUNCTYPE(Bool, RimeSessionId, POINTER(RimeContext))),
        ("free_context", CFUNCTYPE(Bool, POINTER(RimeContext))),
        ("get_status", CFUNCTYPE(Bool, RimeSessionId, POINTER(RimeStatus))),
        ("free_status", CFUNCTYPE(Bool, POINTER(RimeStatus))),
        ("set_option", CFUNCTYPE(None, RimeSessionId, c_char_p, Bool)),
        ("get_option", CFUNCTYPE(Bool, RimeSessionId, c_char_p)),
        ("set_property", CFUNCTYPE(None, RimeSessionId, c_char_p, c_char_p)),
        ("get_property", CFUNCTYPE(Bool, RimeSessionId, c_char_p, c_char_p, c_size_t)),
        ("get_schema_list", CFUNCTYPE(Bool, POINTER(RimeSchemaList))),
        ("free_schema_list", CFUNCTYPE(None, POINTER(RimeSchemaList))),
        ("get_current_schema", CFUNCTYPE(Bool, RimeSessionId, c_char_p, c_size_t)),
        ("select_schema", CFUNCTYPE(Bool, RimeSessionId, c_char_p)),
        ("schema_open", CFUNCTYPE(Bool, c_char_p, POINTER(RimeConfig))),
        ("config_open", CFUNCTYPE(Bool, c_char_p, POINTER(RimeConfig))),
        ("config_close", CFUNCTYPE(Bool, POINTER(RimeConfig))),
        ("config_get_bool", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, POINTER(Bool))),
        ("config_get_int", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, POINTER(c_int))),
        ("config_get_double", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, POINTER(c_double))),
        ("config_get_string", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, c_char_p, c_size_t)),
        ("config_get_cstring", CFUNCTYPE(c_char_p, POINTER(RimeConfig), c_char_p)),
        ("config_update_signature", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p)),
        ("config_begin_map", CFUNCTYPE(Bool, POINTER(RimeConfigIterator), POINTER(RimeConfig), c_char_p)),
        ("config_next", CFUNCTYPE(Bool, POINTER(RimeConfigIterator))),
        ("config_end", CFUNCTYPE(None, POINTER(RimeConfigIterator))),
        ("simulate_key_sequence", CFUNCTYPE(Bool, RimeSessionId, c_char_p)),
        ("register_module", CFUNCTYPE(Bool, POINTER(RimeModule))),
        ("find_module", CFUNCTYPE(POINTER(RimeModule), c_char_p)),
        ("run_task", CFUNCTYPE(Bool, c_char_p)),
        ("get_shared_data_dir", CFUNCTYPE(c_char_p)),
        ("get_user_data_dir", CFUNCTYPE(c_char_p)),
        ("get_sync_dir", CFUNCTYPE(c_char_p)),
        ("get_user_id", CFUNCTYPE(c_char_p)),
        ("get_user_data_sync_dir", CFUNCTYPE(None, c_char_p, c_size_t)),
        ("config_init", CFUNCTYPE(Bool, POINTER(RimeConfig))),
        ("config_load_string", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p)),
        ("config_set_bool", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, Bool)),
        ("config_set_int", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, c_int)),
        ("config_set_double", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, c_double)),
        ("config_set_string", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, c_char_p)),
        ("config_get_item", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, POINTER(RimeConfig))),
        ("config_set_item", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p, POINTER(RimeConfig))),
        ("config_clear", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p)),
        ("config_create_list", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p)),
        ("config_create_map", CFUNCTYPE(Bool, POINTER(RimeConfig), c_char_p)),
        ("config_list_size", CFUNCTYPE(c_size_t, POINTER(RimeConfig), c_char_p)),
        ("config_begin_list", CFUNCTYPE(Bool, POINTER(RimeConfigIterator), POINTER(RimeConfig), c_char_p)),
        ("get_input", CFUNCTYPE(c_char_p, RimeSessionId)),
        ("get_caret_pos", CFUNCTYPE(c_size_t, RimeSessionId)),
        ("select_candidate", CFUNCTYPE(Bool, RimeSessionId, c_size_t)),
        ("get_version", CFUNCTYPE(c_char_p)),
        ("set_caret_pos", CFUNCTYPE(None, RimeSessionId, c_size_t)),
        ("select_candidate_on_current_page", CFUNCTYPE(Bool, RimeSessionId, c_size_t)),
        ("candidate_list_begin", CFUNCTYPE(Bool, RimeSessionId, POINTER(RimeCandidateListIterator))),
        ("candidate_list_next", CFUNCTYPE(Bool, POINTER(RimeCandidateListIterator))),
        ("candidate_list_end", CFUNCTYPE(None, POINTER(RimeCandidateListIterator))),
        ("user_config_open", CFUNCTYPE(Bool, c_char_p, POINTER(RimeConfig))),
        ("candidate_list_from_index", CFUNCTYPE(Bool, RimeSessionId, POINTER(RimeCandidateListIterator), c_int)),
        ("get_prebuilt_data_dir", CFUNCTYPE(c_char_p)),
        ("get_staging_dir", CFUNCTYPE(c_char_p)),
        ("commit_proto", CFUNCTYPE(None, RimeSessionId, c_void_p)),
        ("context_proto", CFUNCTYPE(None, RimeSessionId, c_void_p)),
        ("status_proto", CFUNCTYPE(None, RimeSessionId, c_void_p)),
        ("get_state_label", CFUNCTYPE(c_char_p, RimeSessionId, c_char_p, Bool)),
        ("delete_candidate", CFUNCTYPE(Bool, RimeSessionId, c_size_t)),
        ("delete_candidate_on_current_page", CFUNCTYPE(Bool, RimeSessionId, c_size_t)),
        ("get_state_label_abbreviated", CFUNCTYPE(RimeStringSlice, RimeSessionId, c_char_p, Bool, Bool)),
        ("set_input", CFUNCTYPE(Bool, RimeSessionId, c_char_p)),
        ("get_shared_data_dir_s", CFUNCTYPE(None, c_char_p, c_size_t)),
        ("get_user_data_dir_s", CFUNCTYPE(None, c_char_p, c_size_t)),
        ("get_prebuilt_data_dir_s", CFUNCTYPE(None, c_char_p, c_size_t)),
        ("get_staging_dir_s", CFUNCTYPE(None, c_char_p, c_size_t)),
        ("get_sync_dir_s", CFUNCTYPE(None, c_char_p, c_size_t)),
        ("highlight_candidate", CFUNCTYPE(Bool, RimeSessionId, c_size_t)),
        ("highlight_candidate_on_current_page", CFUNCTYPE(Bool, RimeSessionId, c_size_t)),
        ("change_page", CFUNCTYPE(Bool, RimeSessionId, Bool)),
    ]


def load_rime_dll(dll_path: str):
    """Load ``rime.dll`` and return ``(cdll, rime_api_contents)``."""
    dll_dir = os.path.dirname(os.path.abspath(dll_path))
    if hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
        os.add_dll_directory(dll_dir)
    lib = CDLL(dll_path)
    lib.rime_get_api.restype = POINTER(RimeApi)
    api_ptr = lib.rime_get_api()
    if not api_ptr:
        raise RuntimeError(f"rime_get_api() returned NULL from {dll_path}")
    return lib, api_ptr.contents
