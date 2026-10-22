"""Simulated camera outage/recovery; no printer connections or controls."""
import asyncio
import os
import time
import unittest
from contextlib import suppress
from unittest.mock import patch

import httpx

with patch.dict(os.environ, {'PRINTER_CONFIG': '/nonexistent/print-view-test.json',
                             'PRINTER_HOST': 'printer.invalid',
                             'PRINTER_SERIAL': 'test', 'PRINTER_PASSWORD': 'test'}):
    import server


class CameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_viewer_survives_outage(self):
        camera = server.Camera()
        camera.viewer_wait = .01
        stream = camera.stream()
        pending = asyncio.create_task(anext(stream))
        # Several former viewer timeouts pass before the camera comes back.
        await asyncio.sleep(.06)
        self.assertFalse(pending.done())
        async with camera.condition:
            camera.frame = b'\xff\xd8recovered\xff\xd9'
            camera.updated = time.monotonic()
            camera.version += 1
            camera.condition.notify_all()
        frame = await asyncio.wait_for(pending, 1)
        self.assertIn(b'recovered', frame)
        await stream.aclose()
        self.assertEqual(camera.viewers, 0)

    async def test_upstream_retries_until_camera_returns(self):
        attempts = 0
        def upstream(request):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise httpx.ConnectError('Simulated printer reboot', request=request)
            return httpx.Response(200, content=b'--frame\r\n\xff\xd8recovered\xff\xd9')
        factory = httpx.AsyncClient
        def client(**kwargs):
            return factory(**kwargs, transport=httpx.MockTransport(upstream))
        camera = server.Camera()
        camera.reconnect_delay = .01
        with patch.object(server.httpx, 'AsyncClient', side_effect=client):
            task = asyncio.create_task(camera.run())
            try:
                for _ in range(100):
                    if camera.version:
                        break
                    await asyncio.sleep(.01)
                self.assertGreaterEqual(attempts, 3)
                self.assertEqual(camera.frame, b'\xff\xd8recovered\xff\xd9')
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
