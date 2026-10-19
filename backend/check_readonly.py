"""Verify cached API, status events, and camera fan-out. No control requests."""
import asyncio
import time
import httpx

BASE = 'http://print-view.local'

async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=10, trust_env=False) as client:
        started = time.perf_counter()
        responses = await asyncio.gather(*[client.get('/api/status') for _ in range(10)])
        assert all(r.status_code == 200 and r.json()['connected'] for r in responses)
        print(f'10 cached status requests: {(time.perf_counter()-started)*1000:.0f} ms total')
        async with client.stream('GET', '/api/events') as events:
            async for line in events.aiter_lines():
                if line.startswith('data: '):
                    print('Status events: OK')
                    break
        async with client.stream('GET', '/camera/') as first:
            assert first.status_code == 200
            one = first.aiter_bytes()
            chunk = await anext(one)
            assert b'Content-Type: image/jpeg' in chunk and b'\xff\xd8' in chunk
            async with client.stream('GET', '/camera/') as second:
                two = second.aiter_bytes()
                assert b'\xff\xd8' in await anext(two)
                health = (await client.get('/api/health')).json()
                assert health['camera_viewers'] >= 2 and health['camera_connected']
                version = health['camera_frames']
                await asyncio.sleep(.5)
                assert (await client.get('/api/health')).json()['camera_frames'] > version
                print(f'Concurrent camera viewers: {health["camera_viewers"]}; frames advancing')
        # The retired raw command proxy must no longer be available.
        assert (await client.get('/mqtt')).status_code == 404
        print('Raw MQTT proxy removed: OK')

asyncio.run(main())
