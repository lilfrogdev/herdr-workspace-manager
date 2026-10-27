import json
import os
import socket


class HerdrError(RuntimeError):
    pass


class Herdr:
    def __init__(self):
        self.path = os.environ.get("HERDR_SOCKET_PATH")
        if os.environ.get("HERDR_ENV") != "1" or not self.path:
            raise HerdrError("Launch inside the attached Herdr session (HERDR_ENV and HERDR_SOCKET_PATH required).")

    def call(self, method, *, timeout=2, **params):
        request = {"id": "herdr-workspace-manager", "method": method, "params": params}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect(self.path)
                connection.sendall(json.dumps(request).encode() + b"\n")
                with connection.makefile("rb") as stream:
                    line = stream.readline(16 * 1024 * 1024)
            response = json.loads(line)
        except (OSError, ValueError) as error:
            raise HerdrError(f"Herdr API: {error}") from error
        if "error" in response:
            error = response["error"]
            raise HerdrError(f"{error['code']}: {error['message']}")
        return response["result"]

    def snapshot(self):
        return self.call("session.snapshot")["snapshot"]

    def process_info(self, pane):
        return self.call("pane.process_info", pane_id=pane, timeout=0.75)["process_info"]

    def create(self, directory, name):
        return self.call("workspace.create", cwd=str(directory), label=name, focus=True)

    def close(self, workspace):
        return self.call("workspace.close", workspace_id=workspace)

    def focus(self, workspace, agent=None):
        if agent:
            self.call("pane.focus", pane_id=agent)
        else:
            self.call("workspace.focus", workspace_id=workspace)
