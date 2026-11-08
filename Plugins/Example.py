"""Quick Info - an example PocketTerm plugin.

Put this file in the Plugins folder (or any sub-folder of it), then open the Plugins page,
press Scan, select it and press Enable. Three buttons will appear under "Plugin actions".

Rules PocketTerm enforces:
  * the file must end in .py and its name must not start with "_"
  * it must define register(app)            -> called once when the plugin is enabled/loaded
  * it may define unregister(app)           -> called when it is disabled, reloaded or PocketTerm closes
  * app is a PocketTermPluginAPI with exactly three methods:
        app.add_action(name, callback, description="")   add a button (callback takes no arguments)
        app.notify(message, title="...")                 show an information dialog
        app.log(message)                                 write a line to Config/pocketterm.log
"""
import os
import platform
import time
from pathlib import Path

# The Plugins folder is the nearest parent of this file called "Plugins".
PLUGINS_DIR = next((p for p in Path(__file__).resolve().parents if p.name.lower() == "plugins"),
                   Path(__file__).resolve().parent)
APP_DIR = PLUGINS_DIR.parent          # the folder that holds Projects, Scripts, Config, ...

_loaded_at = None


def _project_count(app):
    projects = APP_DIR / "Projects"
    try:
        names = sorted(p.name for p in projects.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError as exc:
        app.notify(f"Could not read {projects}:\n{exc}", "Projects")
        return
    preview = "\n".join(names[:15]) + ("\n..." if len(names) > 15 else "")
    app.notify(f"{len(names)} project(s) in\n{projects}\n\n{preview}".strip(), "Projects")


def _system_snapshot(app):
    lines = [f"OS: {platform.system()} {platform.release()}",
             f"Python: {platform.python_version()}",
             f"CPU cores: {os.cpu_count()}"]
    try:
        import psutil  # optional; the plugin works without it
        lines.append(f"CPU use: {psutil.cpu_percent(interval=0.2):.0f}%")
        lines.append(f"Memory use: {psutil.virtual_memory().percent:.0f}%")
    except ImportError:
        lines.append("(install psutil for CPU and memory use)")
    app.notify("\n".join(lines), "System snapshot")


def _uptime(app):
    seconds = int(time.time() - _loaded_at) if _loaded_at else 0
    minutes, secs = divmod(seconds, 60)
    app.notify(f"This plugin has been loaded for {minutes}m {secs}s.", "Plugin uptime")
    app.log(f"uptime checked: {seconds}s")


def register(app):
    """Called by PocketTerm when the plugin is enabled. Register buttons here."""
    global _loaded_at
    _loaded_at = time.time()
    app.add_action("Project count", lambda: _project_count(app), "List the folders in Projects")
    app.add_action("System snapshot", lambda: _system_snapshot(app), "Show OS, Python and CPU/memory info")
    app.add_action("Plugin uptime", lambda: _uptime(app), "How long this plugin has been loaded")
    app.log("Quick Info plugin registered")


def unregister(app):
    """Optional clean-up hook. PocketTerm removes the buttons itself; free anything else you opened."""
    app.log("Quick Info plugin unregistered")
