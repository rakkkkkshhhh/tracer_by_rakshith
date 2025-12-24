# demo_fx/pdyn.py
"""
Public API: register_eval_hook(callback) or register_default().

By default this tries to import the optional C-extension `dynamo_hook` (if you compile it),
and uses that to register the callback. If the extension is not available, this module
falls back to the pure-Python profiling hook in pdyn_profile.py for portability.
"""
try:
    import dynamo_hook  # optional compiled extension
    HAS_C_EXT = True
except Exception:
    dynamo_hook = None
    HAS_C_EXT = False

from .pdyn_profile import register as _py_register, unregister as _py_unregister
from .dynamo_manager import on_call as _default_callback

def register():
    """Register the runtime hook. Prefer compiled ext if available."""
    if HAS_C_EXT:
        # dynamo_hook.set_callback expects a Python callable
        dynamo_hook.set_callback(_default_callback)
        return "c_ext"
    else:
        _py_register()
        return "py_profile"

def unregister():
    if HAS_C_EXT:
        try:
            dynamo_hook.set_callback(None)
        except Exception:
            pass
    else:
        _py_unregister()
