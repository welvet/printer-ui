"""Install the single-worker backend as a macOS login service (no sudo)."""
import os
from pathlib import Path
import plistlib
import subprocess
import time

root = Path(__file__).resolve().parents[1]
python = root / 'backend/.venv/bin/python'
if not python.exists():
    raise SystemExit('Create backend/.venv and install backend/requirements.txt first.')
config = Path.home() / '.config/print-view/config.json'
if not config.exists():
    raise SystemExit('Copy backend/config.example.json to ~/.config/print-view/config.json and fill it in first.')
config.chmod(0o600)
label = 'local.print-view.backend'
plist = Path.home() / f'Library/LaunchAgents/{label}.plist'
plist.parent.mkdir(parents=True, exist_ok=True)
plist.write_bytes(plistlib.dumps({
    'Label': label,
    'ProgramArguments': [str(python), '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '8097', '--workers', '1'],
    'WorkingDirectory': str(root / 'backend'),
    'EnvironmentVariables': {'PRINTER_CONFIG': str(config)},
    'RunAtLoad': True,
    'KeepAlive': True,
    'ThrottleInterval': 10,
    'StandardOutPath': str(root / 'backend/server.log'),
    'StandardErrorPath': str(root / 'backend/server-error.log'),
}))
domain = f'gui/{os.getuid()}'
subprocess.run(['launchctl', 'bootout', f'{domain}/{label}'], capture_output=True)
for _ in range(50):
    if subprocess.run(['launchctl', 'print', f'{domain}/{label}'], capture_output=True).returncode != 0:
        break
    time.sleep(0.1)
subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
print(f'Installed {plist}. Starts at login after reboot; restarts automatically after crashes.')
