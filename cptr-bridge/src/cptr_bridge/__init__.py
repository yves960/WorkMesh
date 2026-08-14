"""cptr-bridge: skeleton OpenAPI tool server for WorkMesh AICP.

Exposes AICP's 6 read-only /agent/* tools on port 8766 so cptr
(Open WebUI Computer) can register it as an OpenAPI tool server.
"""

__version__ = "0.1.0"

from .server import app, main  # noqa: F401
