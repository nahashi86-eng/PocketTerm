import os
import sys
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKGROUND_SCRIPT = os.path.join(BASE_DIR, "runner_background.py")


def start_background_script():

    if not os.path.isfile(BACKGROUND_SCRIPT):
        print("Background script not found!")
        return None

    try:
        return subprocess.Popen(
            [sys.executable, BACKGROUND_SCRIPT],
            cwd=BASE_DIR
        )

    except Exception as error:
        print("Background script error:", error)
        return None