import os, shutil

os.makedirs("bot", exist_ok=True)
os.makedirs("web/css", exist_ok=True)
os.makedirs("web/js", exist_ok=True)

files_to_move = [
    "main.py",
    "gki.py",
    "userbot.py",
    "config.py",
    "permissions.py",
    "oki.py"
]

for filename in files_to_move:
    if os.path.exists(filename):
        shutil.move(filename, os.path.join("bot", filename))
        print(f"Moved {filename} to bot/")
