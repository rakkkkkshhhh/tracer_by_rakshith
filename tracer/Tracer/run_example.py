# # run_example.py
# from demo_fx.tracer import symbolic_trace
# from examples.simple_model import simple_forward
# import demo_fx.ops as ops
# from demo_fx.bytecode_tracer import TraceOnlyBytecodeTracer

# def main():
#    # Trace the function with "example" inputs (we use numbers - proxies are created)
#     gm = symbolic_trace(simple_forward, example_inputs=(0.0, 1.0, 0.0))

#     print("=== Traced Graph ===")
#     print(gm.graph)

#     # Execute the traced graph with concrete inputs
#     out = gm.forward(3.0, 2.0, 0.5)  # x=3.0, scale=2.0, bias=0.5

#     print("=== Execution result ===")
#     print("Output:", out)
    
# if __name__ == "__main__":
#     main()


# # run_example.py
# from demo_fx.pdyn import register, unregister
# from examples.simple_model import simple_forward
# import time

# def main():
#     tr = BytecodeTracer()
#     tr.start()
#     source = register()
#     print("Registered pdyn via:", source)
#     # First call — runtime hook will attempt to trace & replace simple_forward
#     print("First call (may trace + patch):", simple_forward(3.0, 2.0, 0.5))
#     tr.stop()
#     print(tr.graph)
#     # Second call — should be using the GraphModule wrapper (if tracing succeeded)
#     print("Second call (should use graph wrapper):", simple_forward(4.0, 2.5, 1.0))
#     unregister()

# if __name__ == "__main__":
#     main()

# run_example.py
from demo_fx.bytecode_tracer import TraceOnlyBytecodeTracer
from examples.control_flow_model import control_flow_forward

def main():
    tracer = TraceOnlyBytecodeTracer()
    graph, found_guard = tracer.trace_function(control_flow_forward)

    print("=== Trace-Only FX Graph (control_flow_forward) ===")
    print(graph)
    print("=== Found Guard / Graph-Break? ===", found_guard)

    if found_guard:
        print("\nNOTE: A guard node or graph-break was inserted "
              "because the tracer detected conditional control flow.\n"
              "In a real TorchDynamo-like system this is where "
              "execution would fall back to Python.")
    else:
        print("\nNo guards found — graph captured straight-line code.")

    # If you want to actually execute the graph (only safe when no guards):
    if not found_guard:
        from demo_fx.graph import GraphModule
        gm = GraphModule(control_flow_forward, graph)
        result = gm.run_with_bindings({'x': 3.0, 'y': 9.0})
        print("\n=== Execution Result ===")
        print("Output:", result)

if __name__ == "__main__":
    main()
