import curses
import os
import queue
import threading
import unicodedata
from pathlib import Path

from .api import HerdrError
from .model import GROUPS
from .preview import draw_preview
from .preview_apps import PreviewApps
from .theme import Theme


def clean(value):
    return "".join(c if c.isprintable() or unicodedata.category(c) == "Co" else "?" for c in str(value))


class Picker:
    def __init__(self, api, store):
        self.api = api
        self.store = store
        self.theme = Theme()
        self.workspaces = []
        self.snapshot = {}
        self.preview_enabled = True
        self.preview_apps = PreviewApps(api)
        self.collapsed = set()
        self.selected = None
        self.rows = []
        self.query = ""
        self.mode = "list"
        self.deleting = None
        self.message = ""
        self.directory = Path.home() / "Repositories"
        if not self.directory.is_dir():
            self.directory = Path.home()
        self.entries = []
        self.browser_index = 0
        self.browser_offset = 0
        self.offset = 0
        self.name = ""
        self.cursor = 0
        self.revision = 0
        self.updates = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.wake = threading.Event()

    def poll(self):
        while not self.stop.is_set():
            revision = self.revision
            try:
                workspaces, snapshot = self.store.observe(self.api.snapshot)
                update = (revision, workspaces, snapshot, None)
            except (HerdrError, OSError, ValueError) as error:
                update = (revision, None, None, str(error))
            try:
                self.updates.get_nowait()
            except queue.Empty:
                pass
            self.updates.put(update)
            self.wake.wait(0.6)
            self.wake.clear()

    def refresh(self):
        try:
            revision, workspaces, snapshot, error = self.updates.get_nowait()
        except queue.Empty:
            return
        if revision != self.revision:
            return
        if error:
            self.message = error
            return
        if not self.workspaces and workspaces:
            self.selected = workspaces[0]["workspace_id"]
        self.workspaces = workspaces
        self.snapshot = snapshot
        if self.message.startswith("Herdr API:"):
            self.message = ""

    def rebuild(self):
        old_rows = self.rows
        old_index = next((i for i, r in enumerate(old_rows) if r[0] == self.selected), 0)
        selected_workspace = self.selected.split(":p")[0] if self.selected else None
        for workspace in self.workspaces:
            if workspace["workspace_id"] == selected_workspace:
                self.collapsed.discard(workspace["group"])
        rows = []
        for group in GROUPS:
            members = []
            for workspace in self.workspaces:
                if workspace["group"] != group:
                    continue
                searchable = " ".join([workspace["label"], workspace["workspace_id"]] + [
                    " ".join(str(a.get(k) or "") for k in (
                        "agent", "name", "pane_id", "title", "terminal_title_stripped", "cwd", "detail"
                    )) for a in workspace["agents"]
                ])
                if self.query.casefold() in searchable.casefold():
                    members.append(workspace)
            heading = [(f"{'▸' if group in self.collapsed else '▾'} {group}", "accent", True),
                       (f" ({len(members)})", "overlay0", False)]
            rows.append((f"group:{group}", heading, None, None))
            if group in self.collapsed:
                continue
            for workspace in members:
                wid = workspace["workspace_id"]
                agents = workspace["agents"]
                icon, role = self.theme.status(agents[0] if agents else {"agent_status": "idle"})
                label = [("  " + icon + " ", role, False),
                         (workspace["label"], "subtext0" if group == "Archived" else "text", True)]
                rows.append((wid, label, workspace, None))
        self.rows = rows
        if self.selected is None:
            self.selected = next((r[0] for r in rows if r[2]), rows[0][0])
        if self.selected not in {r[0] for r in rows}:
            parent = self.selected.split(":p")[0] if self.selected and ":p" in self.selected else None
            self.selected = parent if parent in {r[0] for r in rows} else rows[min(old_index, len(rows) - 1)][0]

    def list_directory(self, directory):
        try:
            directory = directory.resolve(strict=True)
            entries = [("[Select this directory]", directory, True), ("../", directory.parent, False)]
            with os.scandir(directory) as scan:
                children = sorted((Path(e.path) for e in scan if e.is_dir()), key=lambda p: p.name.casefold())
            entries.extend((p.name + "/", p, False) for p in children)
        except OSError as error:
            self.message = str(error)
            return
        self.directory = directory
        self.entries = entries
        self.browser_index = 0
        self.browser_offset = 0
        self.mode = "browse"

    def roots(self):
        paths = [Path.home() / "Repositories", Path.home(), Path.cwd()] + self.store.recent()
        paths = list(dict.fromkeys(p for p in paths if p.is_dir()))
        self.entries = [(str(p), p, False) for p in paths]
        self.browser_index = 0
        self.browser_offset = 0
        self.mode = "roots"

    def choose_directory(self, directory):
        self.directory = directory
        self.name = directory.name or "workspace"
        self.cursor = len(self.name)
        self.mode = "name"

    def jump(self, workspace, agent):
        if agent is None and workspace["agents"]:
            agent = workspace["agents"][0]
        pane_id = agent["pane_id"] if agent else None
        self.api.focus(workspace["workspace_id"], pane_id)
        if pane_id:
            self.store.review(pane_id)
        return True

    def key(self, key):
        if self.mode == "delete":
            if key in ("\x1b", "n", "N", "\n", "\r", curses.KEY_ENTER):
                self.mode = "list"
                self.deleting = None
            elif key == "y":
                workspace_id = self.deleting["workspace_id"]
                self.mode = "list"
                self.deleting = None
                self.revision += 1
                self.workspaces, self.snapshot = self.store.observe(self.api.snapshot)
                target = next((w for w in self.workspaces if w["workspace_id"] == workspace_id), None)
                if target is None:
                    self.message = "Workspace no longer exists"
                elif target["group"] != "Archived":
                    self.message = "Workspace is no longer archived; deletion cancelled"
                else:
                    self.api.close(workspace_id)
                    self.revision += 1
                    self.workspaces = [w for w in self.workspaces if w["workspace_id"] != workspace_id]
                    self.message = ""
                self.wake.set()
            return False
        if self.mode == "search":

            if key == "\x1b":
                self.query = ""
                self.mode = "list"
            elif key in ("\n", "\r", curses.KEY_ENTER):
                self.mode = "list"
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                self.query = self.query[:-1]
            elif isinstance(key, str) and key.isprintable():
                self.query += key
            return False
        if self.mode == "name":
            if key == "\x1b":
                self.mode = "list"
            elif key in ("\n", "\r", curses.KEY_ENTER):
                if not self.name.strip():
                    self.message = "Workspace name cannot be empty"
                elif not self.directory.is_dir():
                    self.message = "Directory no longer exists"
                else:
                    self.api.create(self.directory, self.name.strip())
                    self.store.recent(self.directory)
                    return True
            elif key == curses.KEY_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif key == curses.KEY_RIGHT:
                self.cursor = min(len(self.name), self.cursor + 1)
            elif key == curses.KEY_HOME or key == "\x01":
                self.cursor = 0
            elif key == curses.KEY_END or key == "\x05":
                self.cursor = len(self.name)
            elif key == "\x15":
                self.name = ""
                self.cursor = 0
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b") and self.cursor:
                self.name = self.name[:self.cursor - 1] + self.name[self.cursor:]
                self.cursor -= 1
            elif key == curses.KEY_DC:
                self.name = self.name[:self.cursor] + self.name[self.cursor + 1:]
            elif isinstance(key, str) and key.isprintable():
                self.name = self.name[:self.cursor] + key + self.name[self.cursor:]
                self.cursor += 1
            return False
        if self.mode in ("browse", "roots"):
            if key == "\x1b":
                self.mode = "list"
            elif key in ("j", curses.KEY_DOWN):
                self.browser_index = min(len(self.entries) - 1, self.browser_index + 1)
            elif key in ("k", curses.KEY_UP):
                self.browser_index = max(0, self.browser_index - 1)
            elif key in ("h", curses.KEY_LEFT) and self.mode == "browse":
                self.list_directory(self.directory.parent)
            elif key == "r":
                self.roots()
            elif self.entries and key in (" ", "l", curses.KEY_RIGHT, "\n", "\r", curses.KEY_ENTER):
                _, directory, select = self.entries[self.browser_index]
                if (key == " " and self.mode == "browse") or (select and key not in ("l", curses.KEY_RIGHT)):
                    self.choose_directory(directory)
                else:
                    self.list_directory(directory)
            return False
        if key == "\x1b":
            return True
        if key == "/":
            self.mode = "search"
            return False
        if key == "p":
            self.preview_enabled = not self.preview_enabled
            return False
        if key == "n":
            self.message = ""
            self.list_directory(self.directory)
            return False
        index = next((i for i, r in enumerate(self.rows) if r[0] == self.selected), 0)
        if key in ("j", curses.KEY_DOWN):
            self.selected = self.rows[min(index + 1, len(self.rows) - 1)][0]
        elif key in ("k", curses.KEY_UP):
            self.selected = self.rows[max(0, index - 1)][0]
        else:
            identity, _, workspace, agent = self.rows[index]
            if key in ("h", "l", curses.KEY_LEFT, curses.KEY_RIGHT):
                expand = key in ("l", curses.KEY_RIGHT)
                if workspace is None:
                    group = identity.removeprefix("group:")
                    self.collapsed.discard(group) if expand else self.collapsed.add(group)
                elif not expand:
                    self.selected = f"group:{workspace['group']}"
            elif key == "a" and workspace:
                self.store.toggle_archive(workspace["workspace_id"])
                self.wake.set()
            elif key == "d" and workspace and workspace["group"] == "Archived":
                self.deleting = dict(workspace)
                self.message = ""
                self.mode = "delete"
            elif key in ("\n", "\r", curses.KEY_ENTER):
                if workspace:
                    return self.jump(workspace, agent)
                group = identity.removeprefix("group:")
                self.collapsed.symmetric_difference_update({group})
        return False

    def draw(self, screen):
        self.theme.refresh()
        screen.bkgd(" ", self.theme.attr())
        screen.erase()
        height, width = screen.getmaxyx()
        if self.mode not in ("list", "search") or not self.preview_enabled or width < 76 or height < 9:
            self.preview_apps.select([])
        cursor = None

        def cells(text):
            return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)

        def put(y, spans, selected=False, centered=False, left=0, columns=None, fill=True):
            if not 0 <= y < height or width < 2:
                return
            columns = max(0, min(width - 1 - left, columns if columns is not None else width - 1))
            if not columns:
                return
            if isinstance(spans, str):
                spans = [(spans, "text", False)]
            length = sum(cells(clean(text)) for text, _, _ in spans)
            x = left + (max(0, (columns - length) // 2) if centered else 0)
            edge = left + columns
            try:
                if fill:
                    screen.addstr(y, left, " " * columns, self.theme.attr(selected=selected))
                for text, role, bold in spans:
                    clipped = ""
                    for character in clean(text):
                        if x + cells(clipped + character) > edge:
                            break
                        clipped += character
                    if clipped:
                        screen.addstr(y, x, clipped, self.theme.attr(role, selected, bold))
                        x += cells(clipped)
                    if x >= edge:
                        break
            except curses.error:
                pass

        def hints(items):
            compact = cells("  ".join(f"{key} {description}" for key, description in items)) > width - 1
            spans = []
            for key, description in items:
                if spans:
                    spans.append(("  ", "overlay0", False))
                spans.append((key, "accent", True))
                if not compact:
                    spans.append((f" {description}", "overlay0", False))
            return spans

        if height < 9 or width < 35:
            put(0, "Resize terminal (minimum 35 × 9)")
            screen.refresh()
            return
        footer = height - 1 - bool(self.message)
        if self.mode in ("browse", "roots"):
            put(0, [(str(self.directory) if self.mode == "browse" else "Recent directories", "accent", True)])
            capacity = footer - 3
            self.browser_offset = max(0, min(self.browser_offset, self.browser_index))
            if self.browser_index >= self.browser_offset + capacity:
                self.browser_offset = self.browser_index - capacity + 1
            for y, entry in enumerate(self.entries[self.browser_offset:self.browser_offset + capacity], 2):
                index = y - 2 + self.browser_offset
                icon = "✓" if entry[2] else "↑" if entry[0] == "../" else "▸"
                put(y, [(f" {icon} ", "green" if entry[2] else "accent", False),
                        (entry[0], "text", False)], index == self.browser_index)
            put(footer, hints([("j/k", "move"), ("↵/l", "open"), ("h", "up"), ("space", "select"), ("r", "recent"), ("esc", "cancel")]), centered=True)
        elif self.mode == "name":
            put(0, [(str(self.directory), "overlay0", False)])
            put(2, [("Workspace name", "accent", True)])
            start = max(0, self.cursor - (width - 5))
            put(3, [(" " + self.name[start:], "text", False)], True)
            cursor = (3, min(width - 2, 1 + cells(self.name[start:self.cursor])))
            put(footer, hints([("↵", "create"), ("←/→", "edit"), ("ctrl+u", "clear"), ("esc", "cancel")]), centered=True)
        elif self.mode == "delete":
            top = max(0, (footer - 5) // 2)
            put(top, [("Delete workspace?", "red", True)], centered=True)
            put(top + 2, [(self.deleting["label"], "text", True),
                          (f"  {self.deleting['workspace_id']}", "overlay0", False)], centered=True)
            put(top + 4, [("Closes panes and stops processes.", "red", False)], centered=True)
            put(footer, hints([("y", "delete"), ("n/esc/↵", "cancel")]), centered=True)
        else:
            show_preview = self.preview_enabled and width >= 76
            list_width = max(38, (width - 4) * 55 // 100) if show_preview else width - 1
            start = 2 if self.query or self.mode == "search" else 0
            if start:
                put(0, [("/ ", "accent", True), (self.query, "text", False)], columns=list_width)
                if self.mode == "search":
                    cursor = (0, min(list_width - 1, 2 + cells(self.query)))
            capacity = footer - start - 1
            index = next((i for i, r in enumerate(self.rows) if r[0] == self.selected), 0)
            self.offset = max(0, min(self.offset, index))
            if index >= self.offset + capacity:
                self.offset = index - capacity + 1
            for y, row in enumerate(self.rows[self.offset:self.offset + capacity], start):
                put(y, row[1], row[0] == self.selected, columns=list_width)
            if show_preview:
                divider = list_width + 1
                left = divider + 2
                for y in range(footer - 1):
                    put(y, [("│", "overlay0", False)], left=divider, columns=1, fill=False)

                def paint(y, x, text, role="text", bold=False, limit=None):
                    remaining = width - 1 - left - x
                    put(y, [(text, role, bold)], left=left + x,
                        columns=min(remaining, limit) if limit is not None else remaining, fill=False)

                workspace, agent = self.rows[index][2:]
                tab_id = workspace["active_tab_id"] if workspace else None
                panes = [pane for pane in self.snapshot.get("panes", []) if pane["tab_id"] == tab_id]
                self.preview_apps.select(panes)
                draw_preview(paint, self.snapshot, workspace, agent, self.theme, width - 1 - left,
                             footer - 1, self.preview_apps)
            items = [("j/k", "move"), ("h/l", "fold"), ("↵", "jump"), ("/", "search"), ("n", "new"), ("a", "archive"), ("p", "preview")]
            if self.rows[index][2] and self.rows[index][2]["group"] == "Archived":
                items.append(("d", "delete"))
            items.append(("esc", "close"))
            put(footer, hints(items), centered=True)
        if self.message:
            put(height - 1, [(self.message, "red", False)])
        try:
            curses.curs_set(1 if cursor else 0)
            if cursor:
                screen.move(*cursor)
        except curses.error:
            pass
        screen.refresh()

    def run(self, screen):
        self.theme.start()
        curses.curs_set(0)
        curses.set_escdelay(25)
        screen.timeout(100)
        thread = threading.Thread(target=self.poll, daemon=True)
        self.preview_apps.start()
        thread.start()
        try:
            while True:
                self.refresh()
                self.rebuild()
                self.draw(screen)
                try:
                    key = screen.get_wch()
                except curses.error:
                    continue
                if key == "\x03":
                    return
                try:
                    if self.key(key):
                        return
                except (HerdrError, OSError, ValueError) as error:
                    self.message = f"{error} (inspect Herdr state before retrying)"
        finally:
            self.stop.set()
            self.wake.set()
            self.preview_apps.close()
            thread.join(timeout=3)
