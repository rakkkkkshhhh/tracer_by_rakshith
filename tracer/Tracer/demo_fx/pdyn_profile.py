# demo_fx/pdyn_profile.py
import sys
import inspect
from types import FunctionType
from .dynamo_manager import on_call

# profiling function (called on call/return/exception)
def _profile_fn(frame, event, arg):
    # We only care about 'call' events
    if event != "call":
        return
    try:
        on_call(frame)
    except Exception:
        # don't let tracer crash user program; be noisy during development
        import traceback
        traceback.print_exc()
    return

def register():
    """Register the Python-level profile hook (portable)."""
    # setprofile affects all threads created after setting in CPython; for production you'd handle threading.
    sys.setprofile(_profile_fn)

def unregister():
    sys.setprofile(None)
