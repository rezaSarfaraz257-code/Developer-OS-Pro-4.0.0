"""Capability-scoped extension API for the Developer OS IDE.

Extensions contribute metadata and commands only. The runner does not execute
extension-provided shell commands directly; host policy decides execution.
"""
from __future__ import annotations
import re

EXTENSION_API_VERSION="1"
BUILTIN_EXTENSIONS={
 "developer-os.core":{"version":"1.0.0","activation":["startup","workspace"],"contributes":["commands","diagnostics","languages"],"permissions":["workspace.read","workspace.write"]},
 "developer-os.test-explorer":{"version":"1.0.0","activation":["workspace"],"contributes":["commands","testProviders"],"permissions":["workspace.read","runner.test"]},
 "developer-os.debugger":{"version":"1.0.0","activation":["debug"],"contributes":["debugAdapters","commands"],"permissions":["workspace.read","debug.session"]},
}
def manifest():
    return {"api_version":EXTENSION_API_VERSION,"extensions":BUILTIN_EXTENSIONS}
def validate_id(extension_id):
    value=str(extension_id or "").strip()
    if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*",value):
        raise ValueError("Invalid extension identifier.")
    return value
