# demo_fx/dynamo_manager.py
import types
import inspect
from .tracer import symbolic_trace
from .graph import GraphModule
from .bytecode_tracer import TraceOnlyBytecodeTracer

_TRACE_CACHE = {}  # fn -> { 'gm': gm, 'wrapper': wrapper, 'owner_ns': owner_ns, 'original': original_fn }

def _get_callable_from_frame(frame):
    code = frame.f_code
    name = code.co_name
    g = frame.f_globals
    candidate = g.get(name)
    if isinstance(candidate, types.FunctionType) and candidate.__code__ is code:
        return candidate, g
    l = frame.f_locals
    cand = l.get(name)
    if isinstance(cand, types.FunctionType) and cand.__code__ is code:
        return cand, l
    for k, v in g.items():
        if isinstance(v, types.FunctionType) and v.__code__ is code:
            return v, g
    return None, None

def _make_guard_checks(guards, original_fn):
    """
    Convert tracer guard tuples into fast callables (bindings -> bool).
    We capture original_fn.__globals__ at trace time for global checks.
    """
    checks = []
    meta = []
    gdict = original_fn.__globals__
    for g in guards:
        tag = g[0]
        if tag == "global_eq":
            _, name, val = g
            def make_check(name, val):
                return lambda bindings, gdict=gdict: gdict.get(name) is val
            checks.append(make_check(name, val))
            meta.append(g)
        elif tag == "deref_eq":
            _, name, val = g
            def make_check(name, val):
                # attempt to read global first (best-effort), else pass conservatively
                return lambda bindings, gdict=gdict: gdict.get(name, val) is val
            checks.append(make_check(name, val))
            meta.append(g)
        elif tag == "attr_eq":
            _, base_node, attr, val = g
            # base_node might be a placeholder node with name
            base_name = getattr(base_node, "name", None)
            def make_check(base_name, attr, val):
                if base_name is None:
                    # cannot check at runtime -> conservative fail by returning False to force fallback
                    return lambda bindings, gdict=gdict: False
                return lambda bindings, gdict=gdict: getattr(bindings.get(base_name, object()), attr, None) is val
            checks.append(make_check(base_name, attr, val))
            meta.append(g)
        elif tag == "is_bool":
            _, cond_node = g
            cond_name = getattr(cond_node, "name", None)
            def make_check(cond_name):
                if cond_name is None:
                    return lambda bindings, gdict=gdict: False
                return lambda bindings, gdict=gdict: isinstance(bindings.get(cond_name), bool)
            checks.append(make_check(cond_name))
            meta.append(g)
        elif tag == "phi_unmerged":
            _, name, vals = g
            # cannot verify -> conservative fail to trigger fallback
            checks.append(lambda bindings, gdict=gdict: False)
            meta.append(g)
        elif tag == "unhandled_opcode":
            checks.append(lambda bindings, gdict=gdict: False)
            meta.append(g)
        else:
            # default conservative
            checks.append(lambda bindings, gdict=gdict: False)
            meta.append(g)
    return checks, meta

def _invalidate_and_retrace(fn, owner_ns):
    """
    Remove cached trace for fn and attempt to re-trace. If successful, patch new wrapper.
    Return (gm, wrapper) on success, or None on failure.
    """
    # restore original if present in cache
    entry = _TRACE_CACHE.pop(fn, None)
    original_fn = None
    if entry:
        original_fn = entry.get("original", fn)
        # restore original in namespace
        owner_ns[fn.__name__] = original_fn
    else:
        original_fn = fn

    # attempt to re-trace via bytecode tracer first
    try:
        tracer = TraceOnlyBytecodeTracer()
        graph, guards = tracer.trace_function(original_fn)
        gm = GraphModule(original_fn, graph)
        # build guard checks
        checks, meta = _make_guard_checks(guards, original_fn)
        gm._guard_checks = checks
        gm._guards_meta = meta
        wrapper = _make_wrapper_from_gm_retracing(gm, original_fn, owner_ns)
        owner_ns[original_fn.__name__] = wrapper
        _TRACE_CACHE[original_fn] = {'gm': gm, 'wrapper': wrapper, 'owner_ns': owner_ns, 'original': original_fn}
        return gm, wrapper
    except Exception:
        # fallback to high-level tracer (this will execute original once)
        try:
            argcount = original_fn.__code__.co_argcount + original_fn.__code__.co_kwonlyargcount
            sym_inputs = tuple([None] * argcount)
            gm = symbolic_trace(original_fn, sym_inputs)
            checks, meta = _make_guard_checks([], original_fn)
            gm._guard_checks = checks
            gm._guards_meta = meta
            wrapper = _make_wrapper_from_gm_retracing(gm, original_fn, owner_ns)
            owner_ns[original_fn.__name__] = wrapper
            _TRACE_CACHE[original_fn] = {'gm': gm, 'wrapper': wrapper, 'owner_ns': owner_ns, 'original': original_fn}
            return gm, wrapper
        except Exception:
            # restore original if available
            if entry and entry.get('original'):
                owner_ns[original_fn.__name__] = entry['original']
            return None

def _make_wrapper_from_gm_retracing(gm: GraphModule, original_fn, owner_ns):
    """
    Create wrapper used after (re)tracing: inline guard checks and call gm.run_with_bindings if they pass.
    On guard fail: call _invalidate_and_retrace and fall back.
    """
    sig = inspect.signature(original_fn)
    def wrapper(*args, **kwargs):
        try:
            bound = sig.bind_partial(*args, **kwargs)
        except TypeError:
            bound = sig.bind(*args, **kwargs)
        for n,p in sig.parameters.items():
            if n not in bound.arguments:
                if p.default is not inspect._empty:
                    bound.arguments[n] = p.default
                elif p.kind == inspect.Parameter.VAR_POSITIONAL:
                    bound.arguments[n] = ()
                elif p.kind == inspect.Parameter.VAR_KEYWORD:
                    bound.arguments[n] = {}
                else:
                    # missing required -> call original
                    return original_fn(*args, **kwargs)
        bindings = dict(bound.arguments)
        # run inline guard checks
        for check in getattr(gm, "_guard_checks", []):
            try:
                ok = check(bindings)
            except Exception:
                ok = False
            if not ok:
                # guard failed: invalidate + re-trace
                res = _invalidate_and_retrace(original_fn, owner_ns)
                if res:
                    new_gm, new_wrapper = res
                    # invoke new wrapper immediately
                    return new_wrapper(*args, **kwargs)
                else:
                    return original_fn(*args, **kwargs)
        # all guards passed -> execute
        try:
            return gm.run_with_bindings(bindings)
        except Exception:
            # executor failed unexpectedly, try to re-trace and fallback
            res = _invalidate_and_retrace(original_fn, owner_ns)
            if res:
                new_gm, new_wrapper = res
                return new_wrapper(*args, **kwargs)
            return original_fn(*args, **kwargs)

    wrapper.__name__ = original_fn.__name__
    wrapper.__doc__ = original_fn.__doc__
    return wrapper

def on_call(frame):
    fn, owner_ns = _get_callable_from_frame(frame)
    if fn is None or owner_ns is None:
        return None
    if fn in _TRACE_CACHE:
        return None
    # Attempt trace-only bytecode tracer
    tracer = TraceOnlyBytecodeTracer()
    graph, guards = tracer.trace_function(fn)
    # build gm and guard checks
    gm = GraphModule(fn, graph)
    checks, meta = _make_guard_checks(guards, fn)
    gm._guard_checks = checks
    gm._guards_meta = meta
    wrapper = _make_wrapper_from_gm_retracing(gm, fn, owner_ns)
    # install wrapper and cache original
    _TRACE_CACHE[fn] = {'gm': gm, 'wrapper': wrapper, 'owner_ns': owner_ns, 'original': fn}
    owner_ns[fn.__name__] = wrapper
    return gm
