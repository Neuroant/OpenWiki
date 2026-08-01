"""Zero-dependency web UI over the wiki and the agent."""

from .server import WikiWebApp, serve

__all__ = ["WikiWebApp", "serve"]
