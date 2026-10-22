"""Encoder lifecycle tests with a fake process; never access a printer."""
import asyncio
from contextlib import suppress
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hls import HLS


class Process:
    returncode = None
    def kill(self):
        self.returncode = -9
    async def wait(self):
        return self.returncode


class HLSTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_on_demand_stops_when_idle_and_restarts(self):
        encoder = HLS(SimpleNamespace(frame=None, updated=0))
        encoder.idle_timeout = .04
        processes = []
        async def start(*args, **kwargs):
            process = Process()
            processes.append(process)
            return process
        with patch('hls.asyncio.create_subprocess_exec', side_effect=start):
            task = asyncio.create_task(encoder.run())
            try:
                await asyncio.sleep(.02)
                self.assertEqual(len(processes), 0)
                encoder.touch()
                encoder.touch()
                await asyncio.sleep(.02)
                self.assertEqual(len(processes), 1)
                self.assertIsNone(processes[0].returncode)
                await asyncio.sleep(.12)
                self.assertEqual(processes[0].returncode, -9)
                self.assertIsNone(encoder.process)
                encoder.touch()
                await asyncio.sleep(.02)
                self.assertEqual(len(processes), 2)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self.assertEqual(processes[-1].returncode, -9)
