# dump_app.py
# Usage:  python dump_app.py YOUR_APP_FOLDER_NAME
import os
import re
import sys

if len(sys.argv) < 2:
    sys.exit("Usage: python dump_app.py YOUR_APP_FOLDER_NAME")
app = sys.argv[1]
if not os.path.isdir(app):
    sys.exit(f"Folder '{app}' not found. Run this from the folder that has manage.py.")

OUT = f"{app}_dump.txt"
SKIP_DIRS = {"__pycache__", "media", "static", ".git", "node_modules", "venv", ".venv"}
EXTS = (".py", ".html", ".txt", ".md")
SECRET = re.compile(r"(SECRET|PASSWORD|API_KEY|TOKEN|PRIVATE)", re.I)
SETTINGS_KEEP = re.compile(r"^(MEDIA_|ASSET_|LOGIN_|COMPANY_BANK|DEFAULT_AUTO_FIELD|INSTALLED_APPS|ROOT_URLCONF)")

# Files from other apps that the new app depends on.
EXTRA = [
    "funding/models.py", "funding/accounting.py", "funding/security.py",
    "funding/urls.py", "funding/admin.py", "funding/forms.py",
    "funding/tests/helpers.py",
    "funding/management/commands/seed_ledger.py",
    "funding/management/commands/ledger_check.py",
    "accounts/models.py", "backoffice/audit.py",
]


def own_files():
    for root, dirs, names in os.walk(app):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            if n.endswith(EXTS):
                yield os.path.join(root, n)


def clean(text):
    return "\n".join("# [redacted]" if SECRET.search(l) else l for l in text.splitlines()) + "\n"


def write(out, path):
    with open(path, encoding="utf-8", errors="replace") as f:
        out.write(f"=================== {path}\n{clean(f.read())}\n")


count = 0
with open(OUT, "w", encoding="utf-8") as out:
    for p in own_files():
        write(out, p)
        count += 1
    for p in EXTRA:
        if os.path.exists(p) and not p.startswith(app + os.sep):
            write(out, p)
            count += 1
    for root, dirs, names in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if "settings.py" in names and "urls.py" in names:  # the project folder
            with open(os.path.join(root, "settings.py"), encoding="utf-8", errors="replace") as f:
                keep = [l for l in f.read().splitlines() if SETTINGS_KEEP.match(l)]
            out.write(f"=================== {root}/settings.py (selected lines only)\n" + "\n".join(keep) + "\n\n")
            write(out, os.path.join(root, "urls.py"))
            count += 1
            break

print(f"Wrote {count} files to {OUT} ({os.path.getsize(OUT) // 1024} KB)")