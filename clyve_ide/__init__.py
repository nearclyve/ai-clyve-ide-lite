"""Clyve IDE - local AI agent with a chat UI.

Package layout:
    clyve_ide.config  - settings (API key, model, workspace)
    clyve_ide.tools   - filesystem / shell / sqlite / http tools
    clyve_ide.agent   - streaming agent loop
    clyve_ide.server  - local HTTP server + JSON API + SSE
"""

from .version import __version__  # noqa: F401  (satu sumber: clyve_ide/version.py)
