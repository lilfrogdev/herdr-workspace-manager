import unicodedata
from pathlib import Path

from .preview_apps import app_identity


def draw_preview(paint, snapshot, workspace, agent, theme, width, height, apps=None):
    if not workspace:
        paint(0, 0, "Layout", "accent", True)
        paint(2, 0, "Select a workspace", "overlay0")
        return
    paint(0, 0, workspace["label"], "accent", True)
    tab_id = agent["tab_id"] if agent else workspace["active_tab_id"]
    layout = next((item for item in snapshot.get("layouts", []) if item["tab_id"] == tab_id), None)
    if not layout or not layout["area"]["width"] or not layout["area"]["height"]:
        paint(2, 0, "Layout unavailable", "overlay0")
        return
    tabs = [tab for tab in snapshot.get("tabs", []) if tab["workspace_id"] == workspace["workspace_id"]]
    tab = next((item for item in tabs if item["tab_id"] == tab_id), {})
    count = len(layout["panes"])
    caption = f"Tab {tab.get('label', tab_id)} · {count} {'pane' if count == 1 else 'panes'}"
    if len(tabs) > 1:
        caption += f" · {tab.get('number', '?')}/{len(tabs)} tabs"
    if layout.get("zoomed"):
        caption += " · zoomed"
    paint(1, 0, caption, "overlay0")
    top, rows = 3, height - 3
    if width < 3 or rows < 3:
        return
    grid = [[0] * width for _ in range(rows)]
    roles = [["overlay0"] * width for _ in range(rows)]
    focused = agent["pane_id"] if agent else layout.get("focused_pane_id")
    area = layout["area"]
    boxes = []
    panes = {pane["pane_id"]: pane for pane in snapshot.get("panes", [])}
    agents = {item["pane_id"]: item for item in workspace["agents"]}
    for pane in layout["panes"]:
        rect = pane["rect"]
        left = max(0, min(width - 1, round((rect["x"] - area["x"]) * (width - 1) / area["width"])))
        right = max(left, min(width - 1, round((rect["x"] + rect["width"] - area["x"]) * (width - 1) / area["width"])))
        upper = max(0, min(rows - 1, round((rect["y"] - area["y"]) * (rows - 1) / area["height"])))
        lower = max(upper, min(rows - 1, round((rect["y"] + rect["height"] - area["y"]) * (rows - 1) / area["height"])))
        state = agents.get(pane["pane_id"], panes.get(pane["pane_id"], {}))
        role = "accent" if pane["pane_id"] == focused else theme.status(state)[1]
        for y in (upper, lower):
            for x in range(left, right + 1):
                grid[y][x] |= (8 if x > left else 0) | (2 if x < right else 0)
                if roles[y][x] != "accent":
                    roles[y][x] = role
        for x in (left, right):
            for y in range(upper, lower + 1):
                grid[y][x] |= (1 if y > upper else 0) | (4 if y < lower else 0)
                if roles[y][x] != "accent":
                    roles[y][x] = role
        boxes.append((pane["pane_id"], left, upper, right, lower))
    glyphs = {1: "│", 2: "─", 3: "└", 4: "│", 5: "│", 6: "┌", 7: "├", 8: "─",
              9: "┘", 10: "─", 11: "┴", 12: "┐", 13: "┤", 14: "┬", 15: "┼"}
    for y, row in enumerate(grid):
        x = 0
        while x < width:
            if not row[x]:
                x += 1
                continue
            start, role = x, roles[y][x]
            text = ""
            while x < width and row[x] and roles[y][x] == role:
                text += glyphs[row[x]]
                x += 1
            paint(top + y, start, text, role)
    for pane_id, left, upper, right, lower in boxes:
        inner_width = right - left - 1
        if inner_width < 2 or lower - upper < 2:
            continue
        pane = panes.get(pane_id, {})
        state = agents.get(pane_id, pane)
        icon, role = theme.status(state)
        title = f" {icon} {pane['label']} " if pane.get("label") else f" {icon} "
        paint(top + upper, left + 1, title, role, pane_id == focused, inner_width)
        app = apps.get(pane_id, pane.get("terminal_id")) if apps is not None else None
        app = app or app_identity({}, state.get("agent"))
        lines = [(f"{app['icon']}  {app['name']}", "text", True)]
        available = lower - upper - 1
        directory = app.get("cwd") or pane.get("foreground_cwd") or pane.get("cwd")
        if directory and (not state.get("attention") or available >= 3):
            lines.append((Path(directory).name or "/", "overlay0", False))
        if state.get("attention"):
            reason = "Failed" if state.get("tokens", {}).get("hwm_failure") else (
                "Needs input" if state.get("agent_status") == "blocked" else "Result ready"
            )
            lines.append((reason, role, False))
        lines = lines[:available]
        first = top + upper + 1 + max(0, (available - len(lines)) // 2)
        for offset, (text, color, bold) in enumerate(lines):
            clipped, cells = "", 0
            for character in text:
                character = character if character.isprintable() or unicodedata.category(character) == "Co" else "?"
                size = 0 if unicodedata.combining(character) else 2 if unicodedata.east_asian_width(character) in "WF" else 1
                if cells + size > inner_width:
                    break
                clipped += character
                cells += size
            padding = max(0, (inner_width - cells) // 2)
            paint(first + offset, left + 1 + padding, clipped, color, bold, inner_width - padding)
