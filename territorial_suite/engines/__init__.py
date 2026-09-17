"""Engines: the domain logic of the plugin.

Every engine works on a :class:`~territorial_suite.core.project_area.ProjectArea`, takes a
:class:`~territorial_suite.core.feedback.Feedback` and returns serialisable results. No
engine touches the GUI, ``iface`` or the layer tree: adapters do that.
"""
