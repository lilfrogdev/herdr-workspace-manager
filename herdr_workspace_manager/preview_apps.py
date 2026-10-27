import threading
from pathlib import Path

from .api import HerdrError


TERMINAL = ("\uf120", "Terminal")
APPLICATION = "\U000f08c6"
AGENT = "\U000f06a9"
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "nu", "pwsh", "powershell"}
HARNESS = {
    "pi": ("\U000f03ff", "Pi"), "claude": ("\U000f0674", "Claude"),
    "codex": ("\uec81", "Codex"), "gemini": ("\U000f0674", "Gemini"),
    "copilot": ("\uec1e", "Copilot"), "opencode": (AGENT, "OpenCode"),
    "cursor": ("\uea85", "Cursor"), "aider": (AGENT, "Aider"),
}
APPLICATIONS = {
    "nvim": ("\ue6ae", "Neovim"), "vim": ("\ue7c5", "Vim"), "vi": ("\ue7c5", "Vi"),
    "hx": ("\uf121", "Helix"), "helix": ("\uf121", "Helix"),
    "lazygit": ("\ue702", "LazyGit"), "git": ("\ue702", "Git"),
    "btop": ("\uf080", "btop"), "htop": ("\uf080", "htop"), "top": ("\uf080", "top"),
    "yazi": ("\uf07b", "Yazi"), "ranger": ("\uf07b", "Ranger"),
    "less": ("\uf15c", "less"), "man": ("\uf15c", "man"),
    "python": ("\ue73c", "Python"), "python3": ("\ue73c", "Python"),
    "node": ("\ue719", "Node.js"), "bun": ("\ue719", "Bun"), "tmux": TERMINAL,
}
PACKAGES = {"claude-code": "claude", "codex": "codex", "pi-coding-agent": "pi",
            "gemini-cli": "gemini", "copilot": "copilot"}


def process_name(process, agent):
    argv = process.get("argv") or []
    candidates = [process.get("name"), process.get("argv0"), argv[0] if argv else None]
    names = [Path(value).name.lower().removesuffix(".exe") for value in candidates if value]
    for name in names:
        if name in APPLICATIONS or name in HARNESS:
            if name in ("node", "bun", "python", "python3") and len(argv) > 1:
                for part in Path(argv[1]).parts:
                    if agent and PACKAGES.get(part) == agent:
                        return agent
            return name
    return next((name for name in names if name not in SHELLS), names[0] if names else "")


def app_identity(info, agent=None):
    processes = info.get("foreground_processes") or []
    named = [(process_name(process, agent), process) for process in processes]
    def priority(item):
        name = item[0]
        if name in APPLICATIONS and name not in {"node", "bun", "python", "python3"}:
            return 0
        if name in HARNESS:
            return 1
        return 3 if name in SHELLS else 2

    name, process = min(named, key=priority) if named else ("", {})
    if name in SHELLS:
        icon, label = TERMINAL
    elif name in APPLICATIONS:
        icon, label = APPLICATIONS[name]
    elif name in HARNESS:
        icon, label = HARNESS[name]
    elif name:
        icon, label = APPLICATION, name
    elif agent:
        icon, label = HARNESS.get(agent, (AGENT, agent.capitalize()))
    else:
        icon, label = TERMINAL
    return {"icon": icon, "name": label, "cwd": process.get("cwd")}


class PreviewApps:
    def __init__(self, api):
        self.api = api
        self.targets = ()
        self.apps = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def select(self, panes):
        targets = tuple((pane["pane_id"], pane["terminal_id"], pane.get("agent")) for pane in panes)
        with self.lock:
            if targets == self.targets:
                return
            self.targets = targets
            self.apps.clear()
        self.wake.set()

    def get(self, pane_id, terminal_id):
        with self.lock:
            return self.apps.get((pane_id, terminal_id))

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            with self.lock:
                targets = self.targets
            for target in targets:
                with self.lock:
                    if self.targets != targets or self.stop.is_set():
                        break
                try:
                    app = app_identity(self.api.process_info(target[0]), target[2])
                except (HerdrError, OSError, ValueError):
                    app = app_identity({}, target[2])
                with self.lock:
                    if self.targets == targets:
                        self.apps[target[:2]] = app
            self.wake.wait(0.8)

    def close(self):
        self.stop.set()
        self.wake.set()
        self.thread.join(timeout=2)
        with self.lock:
            self.targets = ()
            self.apps.clear()
