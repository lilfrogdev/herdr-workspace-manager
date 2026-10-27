import curses
import json
import os
import re
import time
import tomllib
from functools import lru_cache
from pathlib import Path


PALETTES = json.loads(Path(__file__).with_name("palettes.json").read_text())
ANSI = {name: index for index, name in enumerate((
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "gray",
    "dark-gray", "light-red", "light-green", "light-yellow", "light-blue", "light-magenta", "light-cyan", "white",
))}
ALIASES = {
    "catppuccin-mocha": "catppuccin", "latte": "catppuccin-latte", "light": "catppuccin-latte",
    "tokyonight": "tokyo-night", "tokyo-day": "tokyo-night-day", "tokyonight-day": "tokyo-night-day",
    "gruvbox-dark": "gruvbox", "onedark": "one-dark", "onelight": "one-light",
    "solarized-dark": "solarized", "lotus": "kanagawa-lotus", "rosepine": "rose-pine",
    "rosepine-dawn": "rose-pine-dawn", "dawn": "rose-pine-dawn",
}


def rgb(value):
    value = str(value).strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    match = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    if match and all(int(c) <= 255 for c in match.groups()):
        return tuple(map(int, match.groups()))
    return None


class Theme:
    def __init__(self):
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        self.path = Path(os.environ.get("HERDR_CONFIG_PATH", root / "herdr/config.toml")).expanduser()
        self.stamp = None
        self.next_check = 0
        self.palette = dict(PALETTES["catppuccin"])
        self.symbols = False
        self.colors = False
        self.pairs = {}
        self.refresh()

    def start(self):
        try:
            curses.start_color()
            curses.use_default_colors()
            self.colors = curses.has_colors()
        except curses.error:
            self.colors = False

    def refresh(self):
        if time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 0.5
        try:
            stamp = self.path.stat().st_mtime_ns
            if stamp == self.stamp:
                return
            config = tomllib.loads(self.path.read_text())
        except FileNotFoundError:
            stamp, config = 0, {}
        except (OSError, ValueError):
            return
        settings = config.get("theme", {})
        name = str(settings.get("name", "catppuccin")).lower().replace("_", "-").replace(" ", "-")
        base = PALETTES.get(ALIASES.get(name, name))
        self.palette = dict(base if isinstance(base, dict) else PALETTES["catppuccin"])
        custom = settings.get("custom", {})
        self.palette.update({k: v for k, v in custom.items() if k in self.palette and isinstance(v, str)})
        ui = config.get("ui", {})
        if ui.get("accent", "cyan") != "cyan" and "accent" not in custom:
            self.palette["accent"] = ui["accent"]
        self.symbols = ui.get("status_indicators") == "symbols"
        self.pairs.clear()
        self.stamp = stamp

    @lru_cache(maxsize=128)
    def color(self, value):
        value = str(value).strip().lower()
        name = value.replace("_", "-").replace(" ", "-")
        if name in ANSI:
            index = ANSI[name]
            return index if index < curses.COLORS else index % 8
        color = rgb(value)
        if color is None:
            return -1
        if curses.COLORS >= 256:
            levels = (0, 95, 135, 175, 215, 255)
            candidates = [(16 + r * 36 + g * 6 + b, (red, green, blue))
                          for r, red in enumerate(levels) for g, green in enumerate(levels) for b, blue in enumerate(levels)]
            candidates += [(232 + i, (8 + i * 10,) * 3) for i in range(24)]
        else:
            candidates = list(enumerate(((0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
                                         (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192))))
        return min(candidates, key=lambda item: sum((a - b) ** 2 for a, b in zip(color, item[1])))[0]

    def attr(self, role="text", selected=False, bold=False):
        modifier = curses.A_BOLD if bold else 0
        if not self.colors:
            return modifier | (curses.A_REVERSE if selected else 0)
        background = self.palette["panel_bg"]
        if selected:
            background = self.palette["selection_bg"]
            if background.lower() in ("reset", "default", "none", "transparent"):
                background = self.palette["active_row_bg"]
        pair = (self.color(self.palette.get(role, self.palette["text"])), self.color(background))
        if pair not in self.pairs:
            index = len(self.pairs) + 1
            try:
                curses.init_pair(index, *pair)
            except (curses.error, ValueError):
                return modifier | (curses.A_REVERSE if selected else 0)
            self.pairs[pair] = index
        return curses.color_pair(self.pairs[pair]) | modifier

    def status(self, agent):
        state = agent.get("agent_status", "unknown")
        if agent.get("tokens", {}).get("hwm_failure") or state == "blocked":
            state = "blocked"
        elif agent.get("attention"):
            state = "done"
        icon = {"working": "◐", "blocked": "×", "done": "✓", "idle": "○"}.get(state, "·") if self.symbols else (
            "●" if state in ("working", "blocked", "done") else "○" if state == "idle" else "·"
        )
        role = {"working": "yellow", "blocked": "red", "done": "teal", "idle": "green"}.get(state, "overlay0")
        return icon, role
