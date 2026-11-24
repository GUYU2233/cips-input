"""Pure-logic core of the cips-input backend.

Four concerns live here, all free of Win32/PIME dependencies so they can be
imported and unit-tested on any platform:

* :mod:`core.normalize` -- clean up clipboard text before it is typed.
* :mod:`core.policy` -- decide how the payload is chunked and paced.
* :mod:`core.profile` -- per-application compatibility settings.
* :mod:`core.strategy` -- map a hotkey action plus a profile to a plan.

Importing this package must never pull in ``ctypes``/``win32`` modules or read
configuration from disk; those belong to the backend layer above.
"""

from __future__ import annotations

from .normalize import MAX_TEXT_LENGTH, normalize_text, should_refuse, strip_edges
from .policy import (  # noqa: F401
    CHUNK_MODES,
    Chunk,
    HUMAN_DEFAULTS,
    normalize_human_settings,
    plan_chunks,
    plan_human_chunks,
    plan_otp_chunks,
)
from .profile import DEFAULT_PROFILE, Profile, ProfileBook
from .strategy import (  # noqa: F401
    ACTIONS,
    DISPATCH_MODES,
    FAST_CHAR_DELAY_MS,
    DispatchPlan,
    resolve_dispatch,
)

__all__ = [
    # normalize
    "normalize_text",
    "strip_edges",
    "should_refuse",
    "MAX_TEXT_LENGTH",
    # policy
    "Chunk",
    "CHUNK_MODES",
    "plan_chunks",
    "plan_otp_chunks",
    "plan_human_chunks",
    "normalize_human_settings",
    "HUMAN_DEFAULTS",
    # profile
    "Profile",
    "ProfileBook",
    "DEFAULT_PROFILE",
    # strategy
    "ACTIONS",
    "DISPATCH_MODES",
    "FAST_CHAR_DELAY_MS",
    "DispatchPlan",
    "resolve_dispatch",
]
