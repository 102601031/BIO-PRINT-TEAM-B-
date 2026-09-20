"""
bioprint_launcher.py — entry point used ONLY for the PyInstaller .exe build.

Running `python app.py` directly (dev mode) is unaffected by this file.
This wraps app.py so that, once frozen into a single .exe:
  - Flask can find templates/static (bundled inside the exe via --add-data)
  - the SQLite database is created next to the .exe, not in the temp
    extraction folder (which PyInstaller deletes on exit)
  - the browser opens automatically, like run_bioprint.bat/.sh do
"""
import os
import sys
import threading
import time
import webbrowser


def _exe_dir() -> str:
    """Folder the .exe itself lives in (persistent), not the temp bundle."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _bundle_dir() -> str:
    """Where bundled resources (templates/static) were extracted to."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


if getattr(sys, "frozen", False):
    # Point the DB at a persistent path next to the .exe BEFORE database.py
    # computes its default path.
    os.environ.setdefault("BIOPRINT_DB", os.path.join(_exe_dir(), "bioprint.db"))

import database as db  # noqa: E402  (import after env var set)

if getattr(sys, "frozen", False) and hasattr(db, "DB_PATH"):
    db.DB_PATH = os.environ["BIOPRINT_DB"]

import app as bioprint_app  # noqa: E402

if getattr(sys, "frozen", False):
    bioprint_app.app.template_folder = os.path.join(_bundle_dir(), "templates")
    bioprint_app.app.static_folder = os.path.join(_bundle_dir(), "static")


def _open_browser(url: str) -> None:
    time.sleep(1.2)
    webbrowser.open(url)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    url = f"http://{host}:{port}/"

    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    print(f"BioPrint is starting at {url}")
    print("Close this window to stop the server.")

    bioprint_app.app.run(
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
    )
