# demo_fx/ops.py
# small collection of pure-Python ops to use in the demo graph
def add(x, y):
    return x + y

def mul(x, y):
    return x * y

def linear(x, w, b=None):
    # Expect x: list/tuple or number; w: scalar or sequence - here we keep it simple: scalar multiply
    # For demo purposes support:
    # - scalar linear: x * w + b
    if isinstance(x, (int, float)):
        r = x * w
        if b is not None:
            r = r + b
        return r
    # otherwise try elementwise for sequences
    try:
        return [xi * w_i for xi, w_i in zip(x, w)]
    except Exception:
        raise RuntimeError("linear: unsupported types in demo ops")
