"""Zero-dependency web UI over the wiki and the agent."""

from .server import AppHolder, WikiWebApp, serve

__all__ = ["AppHolder", "WikiWebApp", "serve"]
