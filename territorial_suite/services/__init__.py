"""Services layer: everything that talks to the outside world.

Depends on :mod:`territorial_suite.core` only. No GUI, no engine logic: a service knows
how to *ask* a server and how to turn the answer into a QGIS-readable dataset, nothing else.
"""
