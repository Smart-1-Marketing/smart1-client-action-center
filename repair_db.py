#!/usr/bin/env python3
import os, sqlite3, shutil, subprocess
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(os.environ.get("DATA_DIR", "/var/data"))
DB = DATA_DIR / "tasks.db"
BACKUPS = DATA_DIR / "db-backups"
BACKUPS.mkdir(parents=True, exist_ok=True)

def quick(path):
    try:
        c=sqlite3.connect(f"file:{path}?mode=ro",uri=True)
        try:return [r[0] for r in c.execute("PRAGMA quick_check").fetchall()]
        finally:c.close()
    except Exception as e:return [f"{type(e).__name__}: {e}"]

print("Database:", DB)
print("Quick check:", quick(DB))
stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
folder=BACKUPS/f"{stamp}-shell-recovery"
folder.mkdir(parents=True,exist_ok=True)
for suffix in ("","-wal","-shm"):
    p=Path(str(DB)+suffix)
    if p.exists():
        shutil.copy2(p, folder/p.name)
print("Safety copy:", folder)

cli=shutil.which("sqlite3")
if not cli:
    raise SystemExit("sqlite3 CLI is not installed. Use a Render disk snapshot or run this recovery on a machine that has sqlite3.")

sql=folder/"recover.sql"
recovered=DATA_DIR/f"tasks.recovered-{stamp}.db"
with sql.open("wb") as out:
    p=subprocess.run([cli,str(DB),".recover --ignore-freelist"],stdout=out,stderr=subprocess.PIPE)
if p.returncode:
    raise SystemExit(p.stderr.decode("utf8","replace"))

with sql.open("rb") as inp:
    p=subprocess.run([cli,str(recovered)],stdin=inp,stderr=subprocess.PIPE)

print("Recovered quick check:", quick(recovered))
print("Recovered DB:", recovered)
print("Original has NOT been replaced automatically by this standalone script.")
