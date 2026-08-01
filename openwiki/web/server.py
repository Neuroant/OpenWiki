"""A zero-dependency web UI over the wiki and the agent (stdlib ``http.server``).

`WikiWebApp` holds the app state (wiki dir, search index, editing agent) and
exposes plain methods; the request handler is a thin JSON/static wrapper around
them. `ThreadingHTTPServer` keeps the UI responsive while a chat turn waits on
Ollama; a lock serializes the (stateful, single-user) agent.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from ..chat_agent import WikiAgent
from ..search import SemanticIndex
from ..tools import WikiTools, _first_heading

STATIC_DIR = Path(__file__).parent / "static"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".md": "text/markdown; charset=utf-8",
}


class WikiWebApp:
    def __init__(self, wiki_dir, index: Optional[SemanticIndex] = None,
                 agent: Optional[WikiAgent] = None, tools: Optional[WikiTools] = None,
                 dry_run: bool = False) -> None:
        self.wiki_dir = Path(wiki_dir)
        self.index = index
        self.tools = tools or WikiTools(wiki_dir, index=index, dry_run=dry_run)
        self.agent = agent
        self._lock = threading.Lock()

    def manifest(self) -> dict:
        manifest_path = self.wiki_dir / "wiki.json"
        if manifest_path.is_file():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        pages = [
            {"slug": f.stem, "title": _first_heading(f), "parent": None, "children": []}
            for f in sorted((self.wiki_dir / "pages").glob("*.md"))
        ]
        return {"title": self.wiki_dir.name, "pages": pages}

    def get_page(self, slug: str) -> dict:
        markdown = self.tools.read_page(slug)
        if markdown.startswith("ERROR"):
            raise KeyError(markdown)
        return {"slug": slug, "markdown": markdown}

    def search(self, query: str, k: int = 8) -> dict:
        if self.index is None:
            raise RuntimeError("No search index is loaded. Run `openwiki index` first.")
        results = self.index.search(query, k=k)
        return {"results": [
            {"score": r.score, "slug": r.page_slug, "title": r.page_title,
             "pdf_page_start": r.pdf_page_start, "pdf_page_end": r.pdf_page_end,
             "text": r.text}
            for r in results
        ]}

    def chat(self, message: str) -> dict:
        if self.agent is None:
            raise RuntimeError("Chat is unavailable (no agent configured).")
        with self._lock:
            turn = self.agent.send(message)
        return {
            "reply": turn.reply,
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "result": c.result}
                for c in turn.tool_calls
            ],
        }


def make_handler(app: WikiWebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenWiki"

        def log_message(self, *args):  # keep the console quiet
            pass

        # -- responders --

        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _bytes(self, data, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _static(self, rel: str):
            target = (STATIC_DIR / rel).resolve()
            root = STATIC_DIR.resolve()
            if target != root and root not in target.parents:
                return self._json({"error": "forbidden"}, 403)
            if not target.is_file():
                return self._json({"error": "not found"}, 404)
            self._bytes(target.read_bytes(), _CONTENT_TYPES.get(target.suffix, "application/octet-stream"))

        # -- routes --

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path == "/":
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/api/wiki":
                    return self._json(app.manifest())
                if path.startswith("/api/pages/"):
                    slug = unquote(path[len("/api/pages/"):])
                    try:
                        return self._json(app.get_page(slug))
                    except KeyError as exc:
                        return self._json({"error": str(exc)}, 404)
                return self._json({"error": "not found"}, 404)
            except Exception as exc:  # never let the handler thread crash
                return self._json({"error": str(exc)}, 500)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                data = self._body_json()
                if path == "/api/search":
                    query = (data.get("query") or "").strip()
                    return self._json(app.search(query, int(data.get("k", 8))) if query else {"results": []})
                if path == "/api/chat":
                    message = (data.get("message") or "").strip()
                    if not message:
                        return self._json({"error": "empty message"}, 400)
                    return self._json(app.chat(message))
                return self._json({"error": "not found"}, 404)
            except RuntimeError as exc:  # service not configured (no index/agent)
                return self._json({"error": str(exc)}, 503)
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)

    return Handler


def serve(app: WikiWebApp, host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{port}"
    print(f"OpenWiki web UI running at {url}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        httpd.server_close()
