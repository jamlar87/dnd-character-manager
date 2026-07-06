"""Check live main.py RACIAL_TRAIT_DESCS after startup initialization."""
import json, sys, os
sys.path.insert(0, '/home/james/dnd-character-manager')

# Before importing main, set up minimal env
os.environ['DND_DB_PATH'] = '/home/james/dnd-character-manager/character.db'

# Import main — this triggers the startup that populates RACIAL_TRAIT_DESCS
# We'll grab the dict before the server starts
import importlib.util
spec = importlib.util.spec_from_file_location('main', '/home/james/dnd-character-manager/main.py')

# Just load the module to see what it exports
import types
# Actually, let's use a simpler approach: replicate the startup logic
sys.path.insert(0, os.path.dirname('/home/james/dnd-character-manager/main.py'))
# Can't import main directly due to httpx etc... 
# Let me use the systemd service to query the running process state
# Or better: just check the actual rendered HTML for a character with subrace

import subprocess
# Find characters in the database
res = subprocess.run(['sqlite3', '/home/james/dnd-character-manager/character.db', 
    '.tables'], capture_output=True, text=True)
print('DB tables:', res.stdout)
print('DB err:', res.stderr)
