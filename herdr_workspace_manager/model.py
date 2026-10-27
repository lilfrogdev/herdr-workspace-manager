import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path


GROUPS = ("Needs attention", "Working", "Idle", "Archived")


def native_completions(snapshot):
    version = re.match(r"(\d+)\.(\d+)\.(\d+)", snapshot["version"])
    return any("completion_seq" in a for a in snapshot["agents"]) or (
        version is not None and tuple(map(int, version.groups())) >= (0, 9, 5)
    )


def activity_panes(snapshot):
    agents = {a["pane_id"]: dict(a) for a in snapshot["agents"]}
    for pane in snapshot["panes"]:
        if pane.get("tokens", {}).get("hwm_failure"):
            agents.setdefault(pane["pane_id"], dict(pane))["tokens"] = pane["tokens"]
    return list(agents.values())


class Store:
    def __init__(self, socket_path):
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "herdr-workspace-manager"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256(os.fsencode(socket_path)).hexdigest()[:20]
        self.path = root / f"{key}.json"
        self.lock = root / f"{key}.lock"
        self.socket_path = socket_path

    @contextmanager
    def transaction(self):
        with self.lock.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            stat = os.stat(self.socket_path)
            generation = f"{stat.st_dev}:{stat.st_ino}:{stat.st_ctime_ns}"
            if self.path.exists():
                data = json.loads(self.path.read_text())
                if data.get("format") != 1:
                    raise ValueError(f"Unsupported metadata format: {self.path}")
            else:
                data = {"format": 1, "recent": []}
            if data.get("generation") != generation:
                data.update(generation=generation, workspaces={}, panes={})
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before or not self.path.exists():
                fd, temporary = tempfile.mkstemp(dir=self.path.parent)
                try:
                    with os.fdopen(fd, "w") as stream:
                        json.dump(data, stream, ensure_ascii=True)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)

    def observe(self, snapshot):
        with self.transaction() as data:
            workspace_ids = {w["workspace_id"] for w in snapshot["workspaces"]}
            data["workspaces"] = {k: v for k, v in data["workspaces"].items() if k in workspace_ids}
            live = set()
            for agent in activity_panes(snapshot):
                pane_id = agent["pane_id"]
                live.add(pane_id)
                identity = [agent.get("terminal_id"), agent.get("agent"), agent.get("agent_session")]
                identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                old = data["panes"].get(pane_id, {})
                if old.get("identity") != identity:
                    old = {"identity": identity}
                state = agent["agent_status"]
                seq = agent.get("state_change_seq", 0)
                if seq < old.get("seq", 0):
                    continue
                event = old.get("event")
                completed = old.get("completed")
                if state == "blocked":
                    event = f"blocked:{seq}"
                native_completion = agent.get("completion_seq")
                if native_completion:
                    completed = f"completion:{native_completion}"
                elif not native_completions(snapshot) and (
                    state == "done" or (state == "idle" and old.get("state") == "working")
                ):
                    completed = f"transition:{seq}"
                failure = agent.get("tokens", {}).get("hwm_failure")
                new_attention = (state == "blocked" and event != old.get("event")) or (
                    completed is not None and completed != old.get("completed")
                ) or (failure is not None and failure != old.get("failure"))
                workspace = data["workspaces"].setdefault(agent["workspace_id"], {})
                if new_attention:
                    workspace["archived"] = False
                old.update(identity=identity, state=state, seq=seq, event=event, completed=completed, failure=failure)
                data["panes"][pane_id] = old
            data["panes"] = {k: v for k, v in data["panes"].items() if k in live}
            previous_focus = data.get("focused_pane")
            focus = snapshot.get("focused_pane_id")
            if previous_focus is not None and focus != previous_focus and focus in data["panes"]:
                pane = data["panes"][focus]
                pane["reviewed"] = pane.get("completed")
            data["focused_pane"] = focus
            return self.view(snapshot, data)

    def view(self, snapshot, data):
        result = []
        for workspace in snapshot["workspaces"]:
            agents = []
            for source in activity_panes(snapshot):
                if source["workspace_id"] != workspace["workspace_id"]:
                    continue
                agent = dict(source)
                meta = data["panes"].get(agent["pane_id"], {})
                pending = meta.get("completed") is not None and meta.get("completed") != meta.get("reviewed")
                state = agent["agent_status"]
                failure = agent.get("tokens", {}).get("hwm_failure")
                agent["attention"] = state == "blocked" or pending or bool(failure)
                agent["detail"] = ("Failed · " + agent.get("tokens", {}).get("hwm_failure_message", "Reported failure")) if failure else (
                    "Waiting for input / approval" if state == "blocked" else (
                        "Completed · unreviewed" if pending else state.capitalize()
                    )
                )
                agents.append(agent)
            agents.sort(key=lambda a: (not a["attention"], a["agent_status"] != "working", a["pane_id"]))
            group = "Needs attention" if any(a["attention"] for a in agents) else (
                "Working" if any(a["agent_status"] == "working" for a in agents) else "Idle"
            )
            archived = data["workspaces"].get(workspace["workspace_id"], {}).get("archived", False)
            result.append(dict(workspace, agents=agents, group="Archived" if archived else group, activity=group))
        return sorted(result, key=lambda w: (GROUPS.index(w["group"]), w["number"]))

    def toggle_archive(self, workspace_id):
        with self.transaction() as data:
            meta = data["workspaces"].setdefault(workspace_id, {})
            meta["archived"] = not meta.get("archived", False)

    def review(self, pane_id):
        with self.transaction() as data:
            pane = data["panes"].get(pane_id)
            if pane:
                pane["reviewed"] = pane.get("completed")

    def recent(self, directory=None):
        with self.transaction() as data:
            if directory is not None:
                path = str(directory)
                data["recent"] = [path] + [p for p in data["recent"] if p != path][:11]
            return [Path(p) for p in data["recent"] if Path(p).is_dir()]
