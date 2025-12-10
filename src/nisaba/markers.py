"""Markers for tool categorization."""


class ToolMarker:
    """Base class for tool markers."""
    pass


class ToolMarkerOptional(ToolMarker):
    """Marker for optional tools (excluded by default)."""
    pass


class ToolMarkerDevOnly(ToolMarker):
    """Marker for development-only tools (only enabled in dev mode)."""
    pass


class ToolMarkerMutating(ToolMarker):
    """Marker for tools that mutate state."""
    pass
