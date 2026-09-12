from __future__ import annotations


def bootstrap_source(host: str, port: int, token: str, timeout_seconds: float) -> str:
    """Return Python source that injects the self-describing ``tools`` object."""

    return f'''\
import json as _pca_json
import socket as _pca_socket
import uuid as _pca_uuid

class _PyCodeActRemoteError(RuntimeError):
    pass

class _PyCodeActRpc:
    def call(self, method, *args, **kwargs):
        request = {{
            "id": _pca_uuid.uuid4().hex,
            "token": {token!r},
            "method": method,
            "args": args,
            "kwargs": kwargs,
        }}
        with _pca_socket.create_connection(({host!r}, {port}), timeout={timeout_seconds!r}) as sock:
            sock.sendall(_pca_json.dumps(request).encode("utf-8") + b"\\n")
            stream = sock.makefile("rb")
            line = stream.readline()
        if not line:
            raise _PyCodeActRemoteError("tool RPC server closed without a response")
        response = _pca_json.loads(line)
        if not response.get("ok"):
            error = response.get("error", {{}})
            error_type = error.get("type", "RemoteError")
            error_message = error.get("message", "tool call failed")
            raise _PyCodeActRemoteError(f"{{error_type}}: {{error_message}}")
        return response.get("result")

_rpc = _PyCodeActRpc()

class _TodoTools:
    """A simple persistent task list."""
    def create(self, name: str, status: str = "pending"):
        """Create a unique task in pending, in_progress, or done state."""
        return _rpc.call("todo.create", name, status=status)
    def get(self, name: str):
        """Return a task by name."""
        return _rpc.call("todo.get", name)
    def update(self, name: str, status: str):
        """Update and return an existing task."""
        return _rpc.call("todo.update", name, status)
    def list(self):
        """List tasks in insertion order."""
        return _rpc.call("todo.list")
    def clear(self) -> None:
        """Remove every task."""
        return _rpc.call("todo.clear")

class _SkillTools:
    """Discover and progressively load Agent Skills."""
    def list(self):
        """List discovered skill metadata."""
        return _rpc.call("skills.list")
    def load(self, name: str):
        """Load complete skill instructions and their base directory."""
        return _rpc.call("skills.load", name)

class _KernelTools:
    """Control the persistent Python kernel."""
    def restart(self):
        """Schedule kernel replacement after the current cell finishes."""
        return _rpc.call("kernel.restart")

class _Tools:
    """Harness services available without import."""
    def __init__(self):
        self.todo = _TodoTools()
        self.skills = _SkillTools()
        self.kernel = _KernelTools()

tools = _Tools()
del _Tools, _TodoTools, _SkillTools, _KernelTools
'''
