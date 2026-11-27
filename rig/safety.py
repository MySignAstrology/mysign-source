"""
Tier classification for the pilot rig.

This used to be a second copy of engine/safety.py, and the copy is what broke.
The engine version grew a deterministic pre-filter, a lazy client, and a
fail-closed path; this one kept none of that, so with no API key — or any API
hiccup — classify() raised straight out of the /sms webhook. A crisis message
produced a 500, no canned reply, no operator page, and silence for the person
who sent it. That is the exact failure the module exists to prevent.

There is now one implementation. This module is a thin re-export so callers and
the study docs keep working.
"""
from engine.safety import (  # noqa: F401
    CRISIS_REPLY,
    Tier,
    classify,
    is_hard_stop,
    prefilter,
)
