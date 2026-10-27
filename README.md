# herdr-workspace-manager

A keyboard-driven workspace picker for Herdr. Switch between projects, see which agents need attention, create workspaces, and archive work you want out of the way.

## Usage

Open with **Ctrl+Space, then Space**, or run `herdr-workspace-manager` inside Herdr.

| Key | Action |
| --- | --- |
| `j` / `k` | Move up/down |
| `h` / `l` | Collapse/expand groups |
| `Enter` | Jump to the workspace’s relevant pane |
| `Esc` | Cancel or close |
| `/` | Search |
| `n` | Create a workspace |
| `a` | Archive/unarchive |
| `d` | Delete an archived workspace (with confirmation) |
| `p` | Hide/show the layout preview |

Workspaces are grouped into **Needs attention**, **Working**, **Idle**, and **Archived**, with live status updates. Only groups expand; workspace rows show just their icon and name. Colors and status icons follow your Herdr theme and preferences.

The right-hand preview is on by default and follows the highlighted workspace's active tab. Each pane shows a Nerd Font app/harness icon, a short name, its directory, and an attention reason when needed. Icons stay neutral; borders show status and highlight the focused pane. App identity comes from Herdr's agent and foreground-process information, not terminal text or titles. Use a Nerd Font for the icons; names remain readable without one.

Press `p` to hide/show the preview; it also hides in narrow popups. Previewing never changes focus or marks results reviewed, and no terminal contents are read.

Focusing a completed agent's pane marks its result reviewed. Input and approval requests stay pending until resolved. Opening the picker does not clear attention.

Archiving only hides a workspace from the active groups; its processes keep running. New attention brings it back automatically, but working activity alone does not.

On an archived workspace, press `d`, then `y` to delete it. This closes its panes and stops their processes. Enter or Esc cancels. If new attention unarchives it during confirmation, deletion is cancelled.

### Create a workspace

Press `n` to browse directories, starting at `~/Repositories`. Use `j/k` to move, `l` or Enter to open a directory, `h` to go up, and Space to select. Press `r` for recent directories and starting points.

Confirm or edit the name, then Enter to create and focus a workspace with a single terminal. Esc cancels without creating anything.

## Install

From this checkout:

```sh
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/bin/herdr-workspace-manager" "$HOME/.local/bin/herdr-workspace-manager"
herdr plugin link "$PWD"
```

Back up your Herdr config before editing:

```sh
config="${HERDR_CONFIG_PATH:-$HOME/.config/herdr/config.toml}"
cp -a "$config" "$config.bak.$(date +%Y%m%d-%H%M%S)"
```

Keep your existing Ctrl+Space prefix and add this binding:

```toml
[[keys.command]]
key = "prefix+space"
type = "plugin_action"
command = "herdr-workspace-manager.open"
```

Apply with `herdr config check` and `herdr server reload-config`. The popup is titled **Workspace Manager** and scales to 50% of the screen width and 45% of its height. Adjust these percentages in `herdr-plugin.toml`, then run `herdr plugin link "$PWD"` again.

### Track activity while the picker is closed

Install the background observer:

```sh
mkdir -p "$HOME/.config/systemd/user"
cp contrib/herdr-workspace-manager-watch.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now herdr-workspace-manager-watch.service
```

The included service targets the default local Herdr session. For another socket, update its `HERDR_SOCKET_PATH` to match the attached session before enabling it. After updating this checkout, restart the observer with `systemctl --user restart herdr-workspace-manager-watch.service`.

## Current limitations

- Older Herdr servers can miss completions that occur entirely between activity checks.
- Failures require explicit reporting: `herdr-workspace-manager --report-failure "$HERDR_PANE_ID" --message 'Description'`. Resolve with `--clear-failure "$HERDR_PANE_ID"`.
- Archive and review state survives picker restarts, but resets when the Herdr server is replaced or restarted.

## Remove

Remove the picker's `[[keys.command]]` block and reload Herdr's config. Then remove the observer and launcher:

```sh
herdr plugin unlink herdr-workspace-manager
systemctl --user disable --now herdr-workspace-manager-watch.service
rm "$HOME/.config/systemd/user/herdr-workspace-manager-watch.service"
systemctl --user daemon-reload
rm "$HOME/.local/bin/herdr-workspace-manager"
```

Optional: delete `${XDG_STATE_HOME:-~/.local/state}/herdr-workspace-manager` to clear saved archive/review state and recent directories. Existing workspaces are unaffected.
