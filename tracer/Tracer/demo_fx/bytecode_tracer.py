# demo_fx/bytecode_tracer.py
import dis
import types
import builtins
from .graph import Graph

# helpers used as runtime targets
def _phi_select(cond, *vals):
    # cond is boolean indicating which branch? We'll convention: if cond True -> choose vals[0] else vals[1] ...
    # But in general phi merging needs a selector per pred; we keep binary-if style for simplicity.
    # Here we assume vals = (true_val, false_val)
    return vals[0] if cond else vals[1]

def _call_with_kwargs(func, *args, **kwargs):
    return func(*args, **kwargs)

class BasicBlock:
    def __init__(self, start_offset):
        self.start_offset = start_offset
        self.instrs = []
        self.succs = []  # list of successor block-start offsets
        self.preds = []  # predecessors
    def __repr__(self):
        return f"BB({self.start_offset}, instrs={len(self.instrs)}, succs={self.succs})"

class TraceOnlyBytecodeTracer:
    """
    Bytecode tracer that:
    - constructs basic blocks
    - symbolically simulates instructions over blocks
    - merges locals with phi-like nodes for binary branches
    - records guards and resolves globals/builtins/closure values when possible
    """
    def __init__(self):
        self.graph = Graph()
        self._blocks = {}  # offset -> BasicBlock
        self._offset2idx = {}
        self._instrs = []
        self._fn = None
        self._guards = []
        self._closed = {}

    def trace_function(self, fn: types.FunctionType):
        self._fn = fn
        code = fn.__code__
        self._instrs = list(dis.get_instructions(code))
        self._offset2idx = {instr.offset: idx for idx, instr in enumerate(self._instrs)}
        # build blocks
        self._build_basic_blocks()
        # map freevars
        if fn.__closure__ and code.co_freevars:
            for name, cell in zip(code.co_freevars, fn.__closure__):
                try:
                    self._closed[name] = cell.cell_contents
                except Exception:
                    self._closed[name] = None
        # create placeholders for params
        argcount = code.co_argcount + code.co_kwonlyargcount
        argnames = list(code.co_varnames[:argcount])
        for name in argnames:
            ph = self.graph.create_node("placeholder", target=name, args=(), kwargs={}, name=name)
        # simulate blocks with simple dataflow: BFS from entry
        entry = min(self._blocks.keys())
        # state per block: mapping local-name -> graph Node; stack top ignored across blocks for simplicity,
        # but we support merging locals via phi nodes.
        block_state = {}
        visited = set()
        worklist = [entry]
        block_in_state = {}
        while worklist:
            off = worklist.pop(0)
            if off in visited:
                continue
            visited.add(off)
            bb = self._blocks[off]
            # incoming state is merge of preds
            if not bb.preds:
                state = {}
            else:
                # merge predecessor states (conservative)
                pred_states = [block_in_state[p] for p in bb.preds if p in block_in_state]
                if not pred_states:
                    state = {}
                else:
                    # merge locals: if all preds have same node -> keep, else create phi
                    state = {}
                    keys = set().union(*[set(ps.keys()) for ps in pred_states])
                    for k in keys:
                        vals = [ps.get(k) for ps in pred_states]
                        # if all equal (by identity) keep val
                        first = vals[0]
                        if all(v is first for v in vals):
                            state[k] = first
                        else:
                            # create phi/select: we only support binary merges for now.
                            # We'll create a call_function node to _phi_select that expects (cond, true_val, false_val)
                            # But we need a selector: simplest heuristic: try to find a boolean condition placeholder
                            # present in state named 'cond' or similar — else create an opaque phi selecting vals[0]
                            cond_candidate = state.get("cond")  # heuristic
                            if cond_candidate is None:
                                # fallback: pick first val (conservative)
                                state[k] = vals[0]
                                # emit a guard that we couldn't merge properly
                                self._guards.append(("phi_unmerged", k, vals))
                            else:
                                phi = self.graph.create_node("call_function", target=_phi_select, args=(cond_candidate, vals[0], vals[1]), kwargs={})
                                state[k] = phi
            # store incoming state
            block_in_state[off] = state
            # symbolically execute block's instructions with local state
            stack = []
            local_map = dict(state)
            for instr in bb.instrs:
                opname = instr.opname
                if opname == "LOAD_FAST":
                    name = instr.argval
                    node = local_map.get(name)
                    if node is None:
                        node = self.graph.create_node("get_local", target=name, args=(), kwargs={}, name=name)
                    stack.append(node)
                elif opname == "STORE_FAST":
                    name = instr.argval
                    val = stack.pop() if stack else None
                    local_map[name] = val
                    self.graph.create_node("store_fast", target=name, args=(val,), kwargs={}, name=name)
                elif opname == "LOAD_CONST":
                    c = instr.argval
                    n = self.graph.create_node("const", target=c, args=(), kwargs={}, name=repr(c))
                    stack.append(n)
                elif opname == "LOAD_GLOBAL":
                    gname = instr.argval
                    val = self._fn.__globals__.get(gname, None)
                    if val is None:
                        # check builtins
                        val = getattr(builtins, gname, None)
                    if val is not None:
                        n = self.graph.create_node("const", target=val, args=(), kwargs={}, name=gname)
                        # guard that global identity remains
                        self._guards.append(("global_eq", gname, val))
                    else:
                        n = self.graph.create_node("const", target=gname, args=(), kwargs={}, name=gname)
                    stack.append(n)
                elif opname == "LOAD_DEREF":
                    name = instr.argval
                    val = self._closed.get(name, None)
                    if val is not None:
                        n = self.graph.create_node("const", target=val, args=(), kwargs={}, name=f"deref_{name}")
                        self._guards.append(("deref_eq", name, val))
                    else:
                        n = self.graph.create_node("deref", target=name, args=(), kwargs={}, name=name)
                    stack.append(n)
                elif opname == "LOAD_ATTR":
                    attr = instr.argval
                    base = stack.pop()
                    base_obj = getattr(base, "target", None)
                    if base_obj is not None and hasattr(base_obj, attr):
                        v = getattr(base_obj, attr)
                        n = self.graph.create_node("const", target=v, args=(), kwargs={}, name=f"{getattr(base_obj,'__name__',repr(base_obj))}.{attr}")
                        self._guards.append(("attr_eq", base, attr, v))
                    else:
                        n = self.graph.create_node("get_attr", target=attr, args=(base,), kwargs={}, name=attr)
                    stack.append(n)
                elif opname == "BUILD_LIST":
                    cnt = instr.arg
                    elems = [stack.pop() for _ in range(cnt)][::-1]
                    n = self.graph.create_node("call_function", target=list, args=tuple(elems), kwargs={}, name=f"list_{cnt}")
                    stack.append(n)
                elif opname == "BUILD_MAP":
                    cnt = instr.arg
                    # CPython pushes key/value pairs; we pop 2*cnt values
                    items = [stack.pop() for _ in range(2*cnt)][::-1]
                    pairs = [(items[i], items[i+1]) for i in range(0, len(items), 2)]
                    # represent as call to dict constructor via consts and pairs (best-effort)
                    n = self.graph.create_node("call_function", target=dict, args=(tuple(pairs),), kwargs={}, name=f"map_{cnt}")
                    stack.append(n)
                elif opname == "UNPACK_EX":
                    # arg: (have_star, after)
                    before = instr.arg >> 8
                    after = instr.arg & 0xff
                    seq = stack.pop()
                    # create get_index nodes for elements; if star we map star to rest as list node (best-effort)
                    total = before + after
                    for idx in range(before):
                        node = self.graph.create_node("get_index", target=idx, args=(seq,), kwargs={}, name=f"unpack_{idx}")
                        stack.append(node)
                    if before < 255:  # naive star support
                        rest = self.graph.create_node("call_function", target=list, args=(seq,), kwargs={}, name="unpack_star")
                        stack.append(rest)
                    for idx in range(after):
                        node = self.graph.create_node("get_index", target=-(after - idx), args=(seq,), kwargs={}, name=f"unpack_{before+idx}")
                        stack.append(node)
                elif opname in ("CALL_FUNCTION", "CALL_METHOD"):
                    argc = instr.arg or 0
                    args = [stack.pop() for _ in range(argc)][::-1]
                    func = stack.pop()
                    func_obj = getattr(func, "target", None)
                    if callable(func_obj):
                        n = self.graph.create_node("call_function", target=func_obj, args=tuple(args), kwargs={}, name=getattr(func_obj, "__name__", None))
                    else:
                        n = self.graph.create_node("call_function", target=_call_with_kwargs, args=(func, *args), kwargs={}, name="call_gen")
                    stack.append(n)
                elif opname == "CALL_FUNCTION_KW":
                    argc = instr.arg or 0
                    kw_names_node = stack.pop()
                    kw_names = getattr(kw_names_node, "target", None)
                    raw = [stack.pop() for _ in range(argc)][::-1]
                    if isinstance(kw_names, tuple):
                        k = len(kw_names)
                        pos = raw[:argc - k]
                        kwvals = raw[argc - k:]
                        kwargs = {n: v for n, v in zip(kw_names, kwvals)}
                    else:
                        pos = raw
                        kwargs = {}
                    func = stack.pop()
                    func_obj = getattr(func, "target", None)
                    if callable(func_obj):
                        n = self.graph.create_node("call_function", target=lambda *a, **kw: func_obj(*a, **kw), args=tuple(pos), kwargs=kwargs, name="call_kw")
                    else:
                        n = self.graph.create_node("call_function", target=_call_with_kwargs, args=(func, *pos), kwargs=kwargs, name="call_kw")
                    stack.append(n)
                elif opname == "CALL_FUNCTION_EX":
                    flags = instr.arg
                    if flags & 0x01:
                        kwargs_node = stack.pop()
                        args_node = stack.pop()
                    else:
                        args_node = stack.pop()
                        kwargs_node = None
                    func = stack.pop()
                    n = self.graph.create_node("call_function", target=_call_with_kwargs, args=(func, args_node, kwargs_node), kwargs={}, name="call_ex")
                    stack.append(n)
                elif opname in ("BINARY_ADD","BINARY_MULTIPLY","BINARY_SUBTRACT","BINARY_TRUE_DIVIDE"):
                    right = stack.pop()
                    left = stack.pop()
                    if opname == "BINARY_ADD":
                        op = lambda a,b: a + b
                    elif opname == "BINARY_MULTIPLY":
                        op = lambda a,b: a * b
                    elif opname == "BINARY_SUBTRACT":
                        op = lambda a,b: a - b
                    else:
                        op = lambda a,b: a / b
                    n = self.graph.create_node("call_function", target=op, args=(left, right), kwargs={}, name="binop")
                    stack.append(n)
                elif opname in ("POP_JUMP_IF_FALSE","POP_JUMP_IF_TRUE"):
                    # conditional: create branch edges handled by CFG; here we merely record cond on stack and continue
                    cond = stack.pop() if stack else None
                    # add guard to ensure cond is bool at runtime (conservative)
                    self._guards.append(("is_bool", cond))
                    # we don't continue linear execution; actual flow is handled by block successors
                    # but create no-op here
                    pass
                elif opname == "RETURN_VALUE":
                    val = stack.pop() if stack else None
                    self.graph.create_node("output", target="output", args=(val,), kwargs={}, name="return")
                    # after return, nothing else in this block is relevant
                    break
                elif opname == "POP_TOP":
                    if stack: stack.pop()
                else:
                    # unhandled: conservative guard and stop block simulation
                    self._guards.append(("unhandled_opcode", opname, instr.offset))
                    return self.graph, self._guards
            # record final locals into block_out state (we store onto block object)
            bb._out_state = local_map = local_map = local_map if 'local_map' in locals() else {}
            # push succs to worklist
        for succ in bb.succs:
            # enqueue successor blocks that haven’t been visited yet
            if succ not in visited and succ not in worklist:
                worklist.append(succ)

        return self.graph, self._guards


    def _build_basic_blocks(self):
        # find leaders: first instr, jump targets, fall-through targets
        leaders = set()
        if not self._instrs:
            return
        leaders.add(self._instrs[0].offset)
        for instr in self._instrs:
            if instr.opname.startswith("JUMP") or instr.opname.startswith("POP_JUMP") or instr.opname in ("FOR_ITER",):
                target = instr.argval
                if isinstance(target, int):
                    leaders.add(target)
                # fall-through leader
                # locate next instr offset
                # careful: next index
            if instr.opname in ("RETURN_VALUE",):
                # next instr is a leader if exists
                pass
        # simpler block split: every instruction that is a jump target or target of jump is a leader; also after a return/jump start new block
        target_offsets = set()
        for instr in self._instrs:
            if instr.opname.startswith("JUMP") or instr.opname.startswith("POP_JUMP") or instr.opname in ("FOR_ITER",):
                if isinstance(instr.argval, int):
                    target_offsets.add(instr.argval)
        for idx, instr in enumerate(self._instrs):
            if instr.offset in target_offsets:
                leaders.add(instr.offset)
            if instr.opname in ("RETURN_VALUE", "JUMP_ABSOLUTE", "JUMP_FORWARD"):
                # next instruction becomes leader if present
                if idx + 1 < len(self._instrs):
                    leaders.add(self._instrs[idx+1].offset)
        # create blocks
        sorted_instrs = self._instrs
        leader_list = sorted(list(leaders))
        # ensure 0 included
        if sorted_instrs[0].offset not in leader_list:
            leader_list.insert(0, sorted_instrs[0].offset)
        # build block ranges
        offsets = [instr.offset for instr in sorted_instrs]
        for i, leader in enumerate(leader_list):
            # find end index
            start_idx = self._offset2idx[leader]
            if i + 1 < len(leader_list):
                end_off = leader_list[i+1]
                end_idx = self._offset2idx[end_off]
            else:
                end_idx = len(sorted_instrs)
            bb = BasicBlock(leader)
            bb.instrs = sorted_instrs[start_idx:end_idx]
            self._blocks[leader] = bb
        # fill succs/preds
        for bb in self._blocks.values():
            if not bb.instrs: continue
            last = bb.instrs[-1]
            if last.opname in ("RETURN_VALUE",):
                bb.succs = []
            elif last.opname.startswith("JUMP") or last.opname.startswith("POP_JUMP") or last.opname == "FOR_ITER":
                target = last.argval
                if isinstance(target, int) and target in self._blocks:
                    bb.succs.append(target)
                # fallthrough for conditional jumps
                idx = self._offset2idx[last.offset]
                if idx + 1 < len(self._instrs):
                    fall = self._instrs[idx+1].offset
                    if fall in self._blocks:
                        bb.succs.append(fall)
            else:
                # normal fallthrough
                idx = self._offset2idx[last.offset]
                if idx + 1 < len(self._instrs):
                    fall = self._instrs[idx+1].offset
                    if fall in self._blocks:
                        bb.succs.append(fall)
        # fill preds
        for off, bb in self._blocks.items():
            for s in bb.succs:
                if s in self._blocks:
                    self._blocks[s].preds.append(off)
