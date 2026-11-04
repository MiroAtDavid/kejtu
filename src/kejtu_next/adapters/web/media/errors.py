"""Finite player failures, independent of the old application."""
class WorkspaceError(ValueError):
    pass

class Cancelled(Exception):
    pass
