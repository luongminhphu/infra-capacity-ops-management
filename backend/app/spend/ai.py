"""Moved to app/shared/ai_transport.py (shared by every module, not only Chi phí).
This old name is an alias of the SAME module object, so `from app.spend import ai` and
monkeypatching `ai.JSON_CALL` keep working."""
import sys

from ..shared import ai_transport as _impl

sys.modules[__name__] = _impl
