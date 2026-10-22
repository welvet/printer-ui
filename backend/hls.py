"""Single shared H.264 encoder, fed from the existing camera frame cache."""
import asyncio
import logging
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg

log = logging.getLogger('uvicorn.error')


class HLS:
    def __init__(self, camera):
        self.camera = camera
        self.directory = tempfile.TemporaryDirectory(prefix='print-view-hls-')
        self.root = Path(self.directory.name)
        self.process = None
        self.idle_timeout = 30
        self.last_request = float('-inf')
        self.demand = asyncio.Event()

    def touch(self):
        self.last_request = time.monotonic()
        self.demand.set()

    def active(self):
        return time.monotonic() - self.last_request < self.idle_timeout

    def clear_segments(self):
        for path in self.root.iterdir():
            path.unlink(missing_ok=True)

    async def run(self):
        try:
            while True:
                await self.demand.wait()
                self.demand.clear()
                if not self.active():
                    continue
                try:
                    self.clear_segments()
                    self.process = await asyncio.create_subprocess_exec(
                        imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                        '-f', 'image2pipe', '-vcodec', 'mjpeg', '-framerate', '10', '-i', 'pipe:0',
                        '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
                        '-pix_fmt', 'yuv420p', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                        '-crf', '23', '-g', '10', '-keyint_min', '10', '-sc_threshold', '0',
                        '-f', 'hls', '-hls_time', '1', '-hls_list_size', '4',
                        '-hls_start_number_source', 'epoch',
                        '-hls_flags', 'delete_segments+independent_segments+temp_file',
                        '-hls_segment_filename', str(self.root / 'segment%d.ts'),
                        str(self.root / 'index.m3u8'),
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL)
                    while self.active():
                        frame = self.camera.frame
                        if frame and time.monotonic() - self.camera.updated < 15:
                            self.process.stdin.write(frame)
                            await asyncio.wait_for(self.process.stdin.drain(), 5)
                        elif self.process.returncode is not None:
                            raise RuntimeError('Video encoder exited')
                        await asyncio.sleep(.1)
                except (OSError, RuntimeError, asyncio.TimeoutError):
                    log.exception('Video encoder restarting')
                finally:
                    if self.process and self.process.returncode is None:
                        self.process.kill()
                        await self.process.wait()
                    self.process = None
                    self.clear_segments()
                if self.active():
                    await asyncio.sleep(2)
                    self.demand.set()
        finally:
            self.directory.cleanup()
