# demo_fx/tracer.py
from typing import Any, Tuple
from .graph import Graph
import demo_fx.ops as ops

# a global current tracer used by Proxy to emit nodes.
_current_tracer = None

def _get_tracer():
    global _current_tracer
    if _current_tracer is None:
        raise RuntimeError("No active tracer")
    return _current_tracer

class Proxy:
    """A proxy object used during tracing. Operations on Proxy create nodes in the active tracer's graph."""

    def __init__(self, node):
        self._node = node  # Node object from Graph

    def __repr__(self):
        return f"Proxy(node={self._node})"

    # binary ops
    def __add__(self, other):
        tracer = _get_tracer()
        return tracer.create_proxy_for_op(ops.add, (self, other), {})

    def __radd__(self, other):
        tracer = _get_tracer()
        return tracer.create_proxy_for_op(ops.add, (other, self), {})

    def __mul__(self, other):
        tracer = _get_tracer()
        return tracer.create_proxy_for_op(ops.mul, (self, other), {})

    def __rmul__(self, other):
        tracer = _get_tracer()
        return tracer.create_proxy_for_op(ops.mul, (other, self), {})

    # allow calling as function (for simulating e.g. layer(x, w, b))
    def __call__(self, *args, **kwargs):
        tracer = _get_tracer()
        return tracer.create_proxy_for_op(self._call_target, args, kwargs)

class Tracer:
    def __init__(self):
        self.graph = Graph()

    def trace(self, fn, example_inputs: Tuple):
        """Run fn once with proxy-wrapped inputs to populate self.graph and return GraphModule."""
        global _current_tracer
        prev = _current_tracer
        _current_tracer = self
        try:
            # create placeholder nodes for inputs
            placeholders = []
            for i in range(len(example_inputs)):
                n = self.graph.create_node(op="placeholder", target=f"arg{i}", args=(), kwargs={}, name=f"arg{i}")
                placeholders.append(n)
            # build proxy objects for placeholders
            proxy_inputs = [Proxy(n) for n in placeholders]
            # call the function with proxies
            result = fn(*proxy_inputs)
            # create an output node
            # if result is Proxy -> get its node, if tuple/list -> wrap, else constant
            if isinstance(result, Proxy):
                self.graph.create_node(op="output", target="output", args=(result._node,), kwargs={})
            else:
                # constant output (rare)
                self.graph.create_node(op="output", target="output", args=(result,), kwargs={})
        finally:
            _current_tracer = prev

        from .graph import GraphModule
        return GraphModule(fn, self.graph)

    def create_proxy_for_op(self, target_fn, args, kwargs):
        """Create a call_function node and return a Proxy to it. Args may include Proxy or constants."""
        # convert Proxy args into Node objects or keep constants
        processed_args = []
        for a in args:
            if isinstance(a, Proxy):
                processed_args.append(a._node)
            else:
                processed_args.append(a)
        processed_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, Proxy):
                processed_kwargs[k] = v._node
            else:
                processed_kwargs[k] = v
        node = self.graph.create_node(op="call_function", target=target_fn, args=tuple(processed_args), kwargs=processed_kwargs)
        return Proxy(node)

def symbolic_trace(fn, example_inputs):
    tr = Tracer()
    gm = tr.trace(fn, example_inputs)
    return gm
