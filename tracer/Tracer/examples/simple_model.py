# examples/simple_model.py
# A tiny "model" implemented as a function using demo_fx.ops
from demo_fx import ops

def simple_forward(x, scale, bias):
    # x is expected to be a scalar here for simplicity
    a = ops.mul(x, scale)      # x * scale
    b = ops.add(a, bias)       # (x * scale) + bias
    return ops.add(b, 1.0)     # add 1.0 for fun
