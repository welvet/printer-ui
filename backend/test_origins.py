"""In-process API tests. No server lifespan, network, or printer commands run."""
import os
import unittest
from unittest.mock import patch

import httpx

# Import with fake configuration so the test never reads real printer credentials.
with patch.dict(os.environ, {'PRINTER_CONFIG': '/nonexistent/print-view-test.json',
                             'PRINTER_HOST': 'printer.invalid',
                             'PRINTER_SERIAL': 'test', 'PRINTER_PASSWORD': 'test'}):
    import server


class OriginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # ASGITransport does not run the app lifespan; MQTT and camera stay off.
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                       base_url='http://backend.invalid')
        self.stop = patch.object(server.printer, 'stop', return_value={'ok': True})
        self.mock_stop = self.stop.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.stop.stop()

    async def test_configured_aliases(self):
        for host in ('print-view.local', 'print-view.lan'):
            for scheme in ('http', 'https'):
                with self.subTest(host=host, scheme=scheme):
                    response = await self.client.post('/api/stop',
                        headers={'Origin': f'{scheme}://{host}'},
                        json={'confirmed': True, 'job_id': 'fake-job'})
                    self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mock_stop.call_count, 4)

    async def test_untrusted_or_missing_origin_rejected(self):
        for origin in (None, 'null', 'https://example.com',
                       'http://print-view.lan.evil.example', 'http://print-view.lan:9999'):
            with self.subTest(origin=origin):
                response = await self.client.post('/api/stop',
                    headers={'Origin': origin} if origin else {},
                    json={'confirmed': True, 'job_id': 'fake-job'})
                self.assertEqual(response.status_code, 403)
        self.mock_stop.assert_not_called()

    async def test_confirmation_still_required(self):
        response = await self.client.post('/api/stop',
            headers={'Origin': 'https://print-view.lan'},
            json={'confirmed': False, 'job_id': 'fake-job'})
        self.assertEqual(response.status_code, 400)
        self.mock_stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
