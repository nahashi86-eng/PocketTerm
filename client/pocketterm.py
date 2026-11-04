
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ==========================================
# POCKETTERM SETTINGS
# ==========================================

VERSION = "0.2.0"

GITHUB_USER = "nahashi86-eng"
GITHUB_REPO = "PocketTerm"
BRANCH = "main"

ARCHITECTURE = "x86_64"

BASE_URL = (
    f"https://raw.githubusercontent.com/"
    f"{GITHUB_USER}/{GITHUB_REPO}/{BRANCH}"
)

# Everything is stored beside this script
INSTALL_DIR = Path(__file__).resolve().parent

DATA_DIR = INSTALL_DIR / "PocketTermData"
CACHE_DIR = DATA_DIR / "cache"
PACKAGES_DIR = DATA_DIR / "packages"


# ==========================================
# DOWNLOAD ENGINE
# ==========================================

def download_file(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"PocketTerm/{VERSION}"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read()

    temporary = destination.with_suffix(
        destination.suffix + ".tmp"
    )

    temporary.write_bytes(content)
    temporary.replace(destination)

    return content


def download_json(url, destination):
    content = download_file(url, destination)
    return json.loads(content.decode("utf-8"))


# ==========================================
# REPOSITORY ENGINE
# ==========================================

def update():
    print("\nPocketTerm Package Manager")
    print("===========================")
    print("Connecting to repository...\n")

    try:
        # Download repository configuration
        repository = download_json(
            f"{BASE_URL}/repository.json",
            CACHE_DIR / "repository.json"
        )

        if repository.get("format") != "pocketterm":
            raise ValueError("Unsupported repository format")

        # Verify supported architecture
        architectures = repository.get("architectures", [])

        if ARCHITECTURE not in architectures:
            raise ValueError(
                f"Architecture {ARCHITECTURE} is unsupported"
            )

        # Get architecture index path
        index_path = repository["index"][ARCHITECTURE]

        # Download package index
        index = download_json(
            f"{BASE_URL}/{index_path}",
            CACHE_DIR / f"{ARCHITECTURE}.json"
        )

        if index.get("architecture") != ARCHITECTURE:
            raise ValueError("Architecture mismatch")

        packages = index.get("packages", [])

        if not isinstance(packages, list):
            raise ValueError("Invalid package index")

        print("Repository:", repository["name"])
        print("Architecture:", ARCHITECTURE)
        print("Packages found:", len(packages))
        print("\nDownloading package metadata...")

        downloaded = 0

        # Download every package's metadata
        for package in packages:
            name = package.get("name")
            metadata = package.get("metadata")

            if not name or not metadata:
                print("Skipping invalid package entry")
                continue

            # Prevent paths escaping the package directory
            if Path(name).name != name:
                print(f"Skipping unsafe package name: {name}")
                continue

            if Path(metadata).is_absolute() or ".." in Path(metadata).parts:
                print(f"Skipping unsafe metadata path: {name}")
                continue

            try:
                package_data = download_json(
                    f"{BASE_URL}/{metadata}",
                    PACKAGES_DIR / f"{name}.json"
                )

                if package_data.get("name") != name:
                    raise ValueError("Package name mismatch")

                downloaded += 1
                print(f"  [OK] {name}")

            except Exception as error:
                print(f"  [FAILED] {name}: {error}")

        print("\nUpdate finished!")
        print(f"Metadata downloaded: {downloaded}/{len(packages)}")
        print(f"Local data: {DATA_DIR}")

    except urllib.error.HTTPError as error:
        print(f"GitHub HTTP error: {error.code}")

    except urllib.error.URLError as error:
        print(f"Connection failed: {error.reason}")

    except (ValueError, KeyError, OSError) as error:
        print(f"Update failed: {error}")

    except Exception as error:
        print(f"Unexpected error: {error}")


# ==========================================
# PACKAGE LIST
# ==========================================

def list_packages():
    index_file = CACHE_DIR / f"{ARCHITECTURE}.json"

    if not index_file.exists():
        print("No package index found. Run update first.")
        return

    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))

        print("\nAvailable packages")
        print("==================")

        for package in index.get("packages", []):
            print("-", package["name"])

    except (OSError, ValueError) as error:
        print("Could not read package index:", error)


# ==========================================
# PACKAGE INFORMATION
# ==========================================

def show_package(name):
    package_file = PACKAGES_DIR / f"{name}.json"

    if not package_file.exists():
        print(f"Package not found: {name}")
        print("Try running update first.")
        return

    try:
        package = json.loads(
            package_file.read_text(encoding="utf-8")
        )

        print("\nPackage information")
        print("===================")

        print("Name:", package.get("name"))
        print("Version:", package.get("version"))
        print("Description:", package.get("description"))
        print("Architecture:", package.get("architecture"))
        print("Type:", package.get("type"))
        print("Source:", package.get("source"))
        print("Dependencies:", package.get("dependencies", []))

    except (OSError, ValueError) as error:
        print("Could not read package:", error)


# ==========================================
# HELP
# ==========================================

def help_menu():
    print("""
PocketTerm Package Manager
Version: 0.2.0

Commands:

  update          Download repository information
  list            List available packages
  info <package>  Display package information
  help            Display this menu

Examples:

  python pocketterm.py update
  python pocketterm.py list
  python pocketterm.py info nano
""")


# ==========================================
# MAIN
# ==========================================

def main():
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)
    PACKAGES_DIR.mkdir(exist_ok=True)

    if len(sys.argv) < 2:
        help_menu()
        return

    command = sys.argv[1].lower()

    if command == "update":
        update()

    elif command == "list":
        list_packages()

    elif command == "info":
        if len(sys.argv) < 3:
            print("Usage: python pocketterm.py info <package>")
        else:
            show_package(sys.argv[2])

    elif command in ("help", "--help", "-h"):
        help_menu()

    else:
        print("Unknown command:", command)
        help_menu()


if __name__ == "__main__":
    main()
