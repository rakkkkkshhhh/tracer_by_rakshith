// dynamo_hook/dynamo_hook.c
#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* Note: Real Dynamo uses _PyInterpreterState_SetEvalFrameFunc or PyInterpreterState_SetEvalFrameFunc.
   The symbol is platform/version dependent. This stub demonstrates the idea:
   - We register a C eval-frame function that calls a Python callback.
   - In practice you'll need to adapt to your Python version and follow PEP-523 details.
*/

static PyObject *py_callback = NULL;

/* A very simple eval-frame function that just delegates to the default evaluator.
   Signature varies across Python versions. This is illustrative only. */
static PyObject *
my_eval_frame(PyThreadState *tstate, PyFrameObject *frame, int throwflag)
{
    /* Call python callback if set: py_callback(frame) */
    if (py_callback && PyCallable_Check(py_callback)) {
        PyObject *args = Py_BuildValue("(O)", (PyObject*)frame);
        PyObject *res = PyObject_CallObject(py_callback, args);
        Py_XDECREF(args);
        /* for demo: ignore res and fallthrough to default evaluation */
        Py_XDECREF(res);
    }
    /* fallback to default evaluator */
    /* Warning: _PyEval_EvalFrameDefault is internal; symbol visibility differs. */
    extern PyObject * _PyEval_EvalFrameDefault(PyThreadState *, PyFrameObject *, int);
    return _PyEval_EvalFrameDefault(tstate, frame, throwflag);
}

static PyObject *
dynamo_set_callback(PyObject *self, PyObject *args)
{
    PyObject *cb;
    if (!PyArg_ParseTuple(args, "O:set_callback", &cb))
        return NULL;
    if (!PyCallable_Check(cb)) {
        PyErr_SetString(PyExc_TypeError, "callback must be callable");
        return NULL;
    }
    Py_XINCREF(cb);
    Py_XDECREF(py_callback);
    py_callback = cb;
    Py_RETURN_NONE;
}

static PyMethodDef DynamoMethods[] = {
    {"set_callback", dynamo_set_callback, METH_VARARGS, "Set python callback(frame)"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef dynamomodule = {
    PyModuleDef_HEAD_INIT,
    "dynamo_hook",
    "Minimal PEP-523 demo hook (illustration only)",
    -1,
    DynamoMethods
};

PyMODINIT_FUNC
PyInit_dynamo_hook(void)
{
    return PyModule_Create(&dynamomodule);
}
