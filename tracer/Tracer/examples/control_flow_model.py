# examples/control_flow_model.py
import demo_fx.ops as ops

def control_flow_forward(x, y):
    # simple arithmetic plus a conditional to trigger guard/graph-break
    z = ops.add(x, y)
    if z > 10:          # control-flow condition
        z = ops.mul(z, 2)
    else:
        z = ops.add(z, 5)
    return z
