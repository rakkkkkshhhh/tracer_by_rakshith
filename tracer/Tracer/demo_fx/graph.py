# demo_fx/graph.py
from typing import Any, List, Dict

class Node:
    def __init__(self, op: str, target: Any, args: tuple, kwargs: dict, name: str = None):
        self.op = op          # e.g. 'call_function', 'placeholder', 'output', 'const', 'guard'
        self.target = target
        self.args = args
        self.kwargs = kwargs
        self.name = name

    def __repr__(self):
        t = getattr(self.target, "__name__", None)
        if t is None:
            t = str(self.target)
        return f"Node(op={self.op}, target={t}, name={self.name}, args={self._short(self.args)})"

    @staticmethod
    def _short(obj):
        s = str(obj)
        return s.replace("\n", "")[:200]

class Graph:
    def __init__(self):
        self.nodes: List[Node] = []

    def create_node(self, op: str, target, args=(), kwargs=None, name=None):
        if kwargs is None:
            kwargs = {}
        node = Node(op=op, target=target, args=args, kwargs=kwargs, name=name)
        self.nodes.append(node)
        return node

    def __iter__(self):
        return iter(self.nodes)

    def placeholders(self):
        return [n for n in self.nodes if n.op == "placeholder"]

    def __repr__(self):
        return "Graph(\n  " + "\n  ".join(repr(n) for n in self.nodes) + "\n)"

class GraphModule:
    def __init__(self, original_fn, graph: Graph):
        self.original = original_fn
        self.graph = graph
        # placeholders by name
        self._placeholders = self.graph.placeholders()
        self._placeholder_by_name: Dict[str, Node] = {n.name: n for n in self._placeholders if n.name}
        # runtime guard-check callables (filled by manager)
        self._guard_checks = []  # list of callables(bindings) -> bool
        # keep guard metadata for debug
        self._guards_meta = []

    def __repr__(self):
        return f"GraphModule(fn={self.original.__name__}, graph={self.graph})"

    def forward(self, *inputs):
        if len(inputs) != len(self._placeholders):
            raise RuntimeError(f"Expected {len(self._placeholders)} inputs, got {len(inputs)}")
        bindings = {}
        for node, val in zip(self._placeholders, inputs):
            if node.name:
                bindings[node.name] = val
            else:
                bindings[node] = val
        return self.run_with_bindings(bindings)

    def run_with_bindings(self, bindings: dict):
        # Executes graph nodes in order; supports 'phi' implemented as call_function to _phi_select
        value_map = {}
        # bind placeholders
        for ph in self._placeholders:
            if ph.name is None:
                raise RuntimeError("Placeholder without name")
            if ph.name not in bindings:
                raise RuntimeError(f"Missing binding for placeholder {ph.name}")
            value_map[ph] = bindings[ph.name]

        def resolve(a):
            if isinstance(a, Node):
                if a not in value_map:
                    raise RuntimeError(f"Node {a} not evaluated")
                return value_map[a]
            if isinstance(a, (tuple, list)):
                return type(a)(resolve(x) for x in a)
            return a

        for node in self.graph.nodes:
            if node.op == "placeholder":
                continue
            if node.op == "const":
                value_map[node] = node.target
            elif node.op == "call_function":
                args = tuple(resolve(x) for x in node.args)
                kwargs = {k: resolve(v) for k, v in node.kwargs.items()}
                value_map[node] = node.target(*args, **kwargs)
            elif node.op == "get_local":
                # target is variable name or underlying node
                value_map[node] = node.target
            elif node.op == "get_index":
                base = resolve(node.args[0])
                idx = node.target
                value_map[node] = base[idx]
            elif node.op == "get_attr":
                base = resolve(node.args[0])
                value_map[node] = getattr(base, node.target)
            elif node.op == "output":
                return resolve(node.args[0])
            elif node.op == "guard":
                # guards are checked upstream in wrapper; here treat as no-op
                value_map[node] = None
            else:
                # fallback raise so we catch unimplemented ops early
                raise NotImplementedError(f"Node op not supported in executor: {node.op}")
        raise RuntimeError("No output node found")
