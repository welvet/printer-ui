"""One MQTT session and one MJPEG upstream, shared by every browser."""
import asyncio
import copy
import json
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

log = logging.getLogger('uvicorn.error')
CONFIG_PATH = Path(os.getenv('PRINTER_CONFIG', str(Path.home() / '.config/print-view/config.json')))
CONFIG = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
HOST = os.getenv('PRINTER_HOST') or CONFIG.get('host')
SERIAL = os.getenv('PRINTER_SERIAL') or CONFIG.get('serial')
PASSWORD = os.getenv('PRINTER_PASSWORD') or CONFIG.get('password')
if not all((HOST, SERIAL, PASSWORD)):
    raise RuntimeError('Configure printer host, serial and password in PRINTER_CONFIG or environment')
# Keep aligned with the nginx host aliases; do not accept arbitrary origins.
ORIGINS = {f'{scheme}://{host}' for scheme in ('http', 'https')
           for host in ('print-view.local', 'print-view.lan')}


def merge(target, delta):
    for key, value in delta.items():
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            merge(target[key], value)
        else:
            target[key] = value


class Printer:
    def __init__(self):
        self.lock = threading.RLock()
        self.client_id = '0cli' + secrets.token_hex(3)
        self.base = f'elegoo/{SERIAL}'
        self.topic = f'{self.base}/{self.client_id}/api_request'
        self.register_topic = f'{self.base}/{self.client_id}_req/register_response'
        self.registered = False
        self.connected_at = 0
        self.updated = 0
        self.data = {}
        self.attributes = {}
        self.sequence = 0
        self.pending = {}
        self.stopping = False
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        self.mqtt.username_pw_set('elegoo', PASSWORD)
        self.mqtt.reconnect_delay_set(2, 15)
        self.mqtt.on_connect = self.on_connect
        self.mqtt.on_subscribe = self.on_subscribe
        self.mqtt.on_disconnect = self.on_disconnect
        self.mqtt.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason, properties):
        if reason.is_failure:
            log.warning('Printer MQTT connection rejected: %s', reason)
            return
        with self.lock:
            self.registered = False
            self.updated = 0
            self.data = {}
            self.connected_at = time.monotonic()
        client.subscribe([(self.register_topic, 0), (f'{self.base}/{self.client_id}/api_response', 0), (f'{self.base}/api_status', 0)])

    def on_subscribe(self, client, userdata, mid, reasons, properties):
        if any(r.is_failure for r in reasons):
            return
        client.publish(f'{self.base}/api_register', json.dumps({'client_id': self.client_id, 'request_id': self.client_id + '_req'}))

    def on_disconnect(self, client, userdata, flags, reason, properties):
        with self.lock:
            self.registered = False

    def send(self, method):
        with self.lock:
            self.sequence += 1
            ident = self.sequence
        result = self.mqtt.publish(self.topic, json.dumps({'id': ident, 'method': method, 'params': {}}), qos=0, retain=False)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError('Printer disconnected')
        return ident

    def on_message(self, client, userdata, message):
        try:
            m = json.loads(message.payload)
            with self.lock:
                if message.topic == self.register_topic:
                    self.registered = m.get('error') == 'ok'
                    if self.registered:
                        self.send(1001)
                        self.send(1002)
                    else:
                        log.warning('Printer registration failed: %s', m.get('error'))
                    return
                method, result = m.get('method'), m.get('result', {})
                if method == 1022 and m.get('id') in self.pending:
                    slot = self.pending[m['id']]
                    slot['result'] = result
                    slot['event'].set()
                if result.get('error_code', 0) != 0:
                    return
                if method == 1001:
                    self.attributes = result
                elif method in (1002, 6000) and self.registered:
                    if method == 1002:
                        self.data = result
                    else:
                        merge(self.data, result)
                    self.updated = time.time()
        except Exception:
            log.exception('Invalid printer message')

    def snapshot(self):
        with self.lock:
            fresh = self.registered and self.updated > time.time() - 25
            return {'connected': fresh, 'updated_at': self.updated, 'data': copy.deepcopy(self.data), 'attributes': copy.deepcopy(self.attributes)}

    async def heartbeat(self):
        while True:
            await asyncio.sleep(5)
            try:
                with self.lock:
                    registered = self.registered
                    retry = self.mqtt.is_connected() and time.monotonic() - self.connected_at > 15
                if registered:
                    self.mqtt.publish(self.topic, '{"type":"PING"}')
                    self.send(1002)
                elif retry:
                    await asyncio.to_thread(self.mqtt.reconnect)
            except (OSError, RuntimeError):
                log.warning('Printer poll failed; waiting for reconnect')

    def stop(self, job_id):
        with self.lock:
            snapshot = self.snapshot()
            job = self.data.get('print_status', {})
            machine = self.data.get('machine_status', {})
            if self.stopping:
                raise HTTPException(409, 'A stop request is already pending')
            if not snapshot['connected'] or not job_id or job_id != job.get('uuid') or machine.get('status') != 2 or job.get('state') in ('complete', 'cancelled', 'error') or machine.get('sub_status') in (2077, 2503, 2504):
                raise HTTPException(409, 'Print changed or printer is unavailable')
            self.stopping = True
            self.sequence += 1
            ident = self.sequence
            slot = {'event': threading.Event(), 'result': None}
            self.pending[ident] = slot
        try:
            result = self.mqtt.publish(self.topic, json.dumps({'id': ident, 'method': 1022, 'params': {}}), qos=0, retain=False)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise HTTPException(503, 'Printer disconnected')
            if not slot['event'].wait(10):
                raise HTTPException(504, 'No stop acknowledgement. Check the printer.')
            if slot['result'].get('error_code') != 0:
                raise HTTPException(502, 'Printer rejected the stop request')
            return {'ok': True}
        finally:
            with self.lock:
                self.pending.pop(ident, None)
                self.stopping = False


class Camera:
    def __init__(self):
        self.frame = None
        self.version = 0
        self.updated = 0
        self.condition = asyncio.Condition()
        self.viewers = 0
        self.upstream_connected = False
        self.viewer_wait = 20
        self.reconnect_delay = 2

    async def run(self):
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=15), trust_env=False) as client:
            while True:
                try:
                    async with client.stream('GET', f'http://{HOST}:8080/') as response:
                        response.raise_for_status()
                        self.upstream_connected = True
                        buffer = bytearray()
                        last_frame = time.monotonic()
                        async for chunk in response.aiter_bytes():
                            if time.monotonic() - last_frame > 15:
                                raise ValueError("Camera stopped producing frames")
                            buffer.extend(chunk)
                            while True:
                                start = buffer.find(b'\xff\xd8')
                                if start < 0:
                                    buffer[:] = buffer[-1:]
                                    break
                                if start:
                                    del buffer[:start]
                                end = buffer.find(b'\xff\xd9', 2)
                                if end < 0:
                                    if len(buffer) > 8_000_000:
                                        raise ValueError('Camera frame too large')
                                    break
                                frame = bytes(buffer[:end + 2])
                                del buffer[:end + 2]
                                async with self.condition:
                                    self.frame = frame
                                    self.updated = time.monotonic()
                                    last_frame = self.updated
                                    self.version += 1
                                    self.condition.notify_all()
                except (httpx.HTTPError, ValueError) as error:
                    log.warning('Camera disconnected (%s); reconnecting', type(error).__name__)
                finally:
                    self.upstream_connected = False
                await asyncio.sleep(self.reconnect_delay)

    async def stream(self):
        self.viewers += 1
        version = -1
        try:
            while True:
                async with self.condition:
                    try:
                        await asyncio.wait_for(self.condition.wait_for(lambda: self.version != version and self.frame is not None and time.monotonic() - self.updated < 15), self.viewer_wait)
                    except asyncio.TimeoutError:
                        # Keep the browser subscribed throughout a printer reboot.
                        # Ending an MJPEG response may leave a frozen image with no error event.
                        continue
                    frame, version = self.frame, self.version
                # Only the latest frame is retained. Slow viewers skip frames instead of buffering.
                yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n'
        finally:
            self.viewers -= 1


printer = Printer()
camera = Camera()


@asynccontextmanager
async def lifespan(app):
    printer.mqtt.connect_async(HOST, 1883, 60)
    printer.mqtt.loop_start()
    tasks = [asyncio.create_task(printer.heartbeat()), asyncio.create_task(camera.run())]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    printer.mqtt.disconnect()
    await asyncio.to_thread(printer.mqtt.loop_stop)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
NO_CACHE = {'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}


@app.get('/api/status')
async def status():
    return JSONResponse(printer.snapshot(), headers=NO_CACHE)


@app.get('/api/events')
async def events():
    async def stream():
        while True:
            yield 'data: ' + json.dumps(printer.snapshot()) + '\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type='text/event-stream', headers=NO_CACHE)


@app.get('/api/health')
async def health():
    return {'printer_connected': printer.snapshot()['connected'], 'camera_connected': camera.upstream_connected and time.monotonic() - camera.updated < 15, 'camera_viewers': camera.viewers, 'camera_frames': camera.version}


@app.get('/camera/')
async def video():
    return StreamingResponse(camera.stream(), media_type='multipart/x-mixed-replace; boundary=frame', headers=NO_CACHE)


class StopRequest(BaseModel):
    confirmed: bool
    job_id: str


@app.post('/api/stop')
async def stop(body: StopRequest, request: Request):
    if request.headers.get('origin') not in ORIGINS:
        raise HTTPException(403, 'Invalid origin')
    if not body.confirmed:
        raise HTTPException(400, 'Confirmation required')
    return await asyncio.to_thread(printer.stop, body.job_id)
