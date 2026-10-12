"""Coverage-guided fuzz targets for the Staan MCP server (the ``fuzz/fuzz_*.py`` modules).

Every target is self-contained so an OSS-Fuzz ``build.sh`` can compile any ``fuzz/fuzz_*.py``
on its own with ``compile_python_fuzzer``. All of them import ``atheris`` only inside
``main()``: the harness logic stays importable and unit-testable on platforms where atheris
ships no wheels (Windows).
"""
