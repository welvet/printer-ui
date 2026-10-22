# Print view

Open http://print-view.local on the local network.

The Next.js frontend exports to `out/`, served directly by nginx. No Next.js server runs.
The Python FastAPI backend runs in `backend/.venv`, listening only on `127.0.0.1:8097`.
It keeps one MQTT connection and one camera stream to the printer. Browsers receive
cached status through server-sent events and the latest JPEG frames through a shared
MJPEG stream. Slow viewers skip frames; they do not build up a video backlog.

## Build and run

```sh
npm ci
npm run build
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
mkdir -p ~/.config/print-view
cp backend/config.example.json ~/.config/print-view/config.json
# Fill in your printer IP, serial and LAN password in that private file.
chmod 600 ~/.config/print-view/config.json
python3 scripts/install-backend-service.py
```

Use one backend worker to preserve the single shared printer connection.
Printer credentials live outside the repository in `~/.config/print-view/config.json`
(mode 0600). `PRINTER_CONFIG` can select a different file. `PRINTER_HOST`,
`PRINTER_SERIAL`, and `PRINTER_PASSWORD` environment variables override its values.
No printer password is embedded in the frontend or committed source.

## Installed services

- Nginx config: `~/.config/nginx/nginx.conf`
- Backend: `~/Library/LaunchAgents/local.print-view.backend.plist`
- Bonjour hostname: `~/Library/LaunchAgents/local.print-view.hostname.plist`
- Backend logs: `backend/server.log` and `backend/server-error.log`

The backend and Bonjour user services start at login, including after reboot. The
backend is kept alive and restarted automatically after crashes. It does not run
before login or while the Mac is asleep. Re-run the installer if you move the project.

Both nginx and the backend must be running for the dashboard to work. Bonjour currently advertises this Mac at
the Mac's LAN address; update the hostname service if that address changes.
Nginx permits localhost and `192.168.8.0/24` only.

Restart the backend after editing Python:

```sh
launchctl kickstart -k gui/$(id -u)/local.print-view.backend
```

## API

- `GET /api/status`: cached attributes, status, freshness timestamp
- `GET /api/events`: shared status events, once per second
- `GET /api/health`: printer/camera connection state and viewer count
- `GET /camera/`: shared MJPEG stream
- `POST /api/stop`: requires the dashboard origin, explicit confirmation, current
  job UUID, fresh printer status, and an active job. Returns printer acknowledgement
  or an error. Requests are never retried automatically.

## Read-only verification

```sh
backend/.venv/bin/python backend/check_readonly.py
```

Checks cached API requests, server-sent events, concurrent camera viewers, advancing
frames, and removal of the raw MQTT proxy. Does not invoke the stop endpoint.

## iPhone video fullscreen

Safari uses a native HLS `<video>` stream at `/camera/hls/index.m3u8` so the
fullscreen button can open the iPhone video player. Other browsers retain MJPEG.
A single FFmpeg encoder (packaged by `imageio-ffmpeg` in the Python venv) reads
from the existing frame cache, so this does not add printer connections. HLS
adds a few seconds of buffering; resolution remains that of the camera source.
Segments are kept in a temporary directory with a bounded rolling playlist and
are removed when the encoder shuts down normally. The backend startup service
also starts the encoder automatically.
