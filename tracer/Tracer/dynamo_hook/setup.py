# dynamo_hook/setup.py
from setuptools import setup, Extension

ext = Extension(
    "dynamo_hook",
    sources=["dynamo_hook.c"],
)

setup(
    name="dynamo_hook",
    version="0.0.1",
    ext_modules=[ext],
)
