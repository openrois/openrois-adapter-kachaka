"""Standalone camera feeder: Kachaka front camera -> v4l2loopback device.

Polls the Kachaka robot's front camera via gRPC and pipes JPEG frames
through ffmpeg into a v4l2loopback device (default /dev/video11). The
device appears as a normal webcam to any application (Electron, Chrome,
Zoom, etc.).

Run this BEFORE launching the Electron app so the camera stream is
already active when the app opens the device:

    python camera_feeder.py
    python camera_feeder.py --device /dev/video11 --fps 10
    python camera_feeder.py --target 192.168.100.203:26400

The v4l2loopback module must be loaded with exclusive_caps=1:

    sudo modprobe v4l2loopback video_nr=11 card_label="CAPF" exclusive_caps=1
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import subprocess
import sys
from typing import Any

from kachaka_api.aio import KachakaApiClient


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Extract width and height from a JPEG SOF0/SOF2 marker."""
    i = 2
    while i + 8 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC2):
            height = (data[i + 5] << 8) | data[i + 6]
            width = (data[i + 7] << 8) | data[i + 8]
            return width, height
        if marker == 0xD9:
            break
        i += 2 + ((data[i + 2] << 8) | data[i + 3])
    return None

logger = logging.getLogger(__name__)

DEFAULT_TARGET = "192.168.100.203:26400"
DEFAULT_DEVICE = "/dev/video11"
DEFAULT_FPS = 10.0


class CameraFeeder:
    """Feed Kachaka front camera JPEG frames into a v4l2loopback device.

    Uses ffmpeg as a pipe: JPEG frames go to ffmpeg stdin, ffmpeg passes
    them through (-c copy) to the v4l2 output muxer, which writes to the
    loopback device. The device advertises MJPG capture to readers while
    this writer is running.
    """

    def __init__(
        self,
        client: KachakaApiClient,
        device: str = DEFAULT_DEVICE,
        fps: float = DEFAULT_FPS,
    ) -> None:
        self._client = client
        self._device = device
        self._fps = fps
        self._proc: Any = None
        self._task: asyncio.Task | None = None
        self._frame_interval = 1.0 / fps

    async def start(self) -> None:
        """Start the ffmpeg subprocess and the feeder loop."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            # Input: JPEG frames from stdin.
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-r", str(self._fps),
            "-i", "-",
            # Output: MJPEG passthrough to the v4l2loopback device.
            "-c", "copy",
            "-f", "v4l2",
            self._device,
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._task = asyncio.create_task(self._run())
        logger.info(
            "CameraFeeder started: front camera @ %.1f fps -> %s",
            self._fps, self._device,
        )

    async def _run(self) -> None:
        """Poll the camera and write JPEG frames to ffmpeg stdin."""
        logged_resolution = False
        try:
            while True:
                try:
                    img = await self._client.get_front_camera_ros_compressed_image()
                except Exception:
                    logger.exception("Camera fetch failed")
                    await asyncio.sleep(self._frame_interval)
                    continue

                data = img.data
                if not data:
                    await asyncio.sleep(self._frame_interval)
                    continue

                if not logged_resolution:
                    dims = _jpeg_dimensions(data)
                    if dims is not None:
                        logger.info(
                            "Native camera resolution: %dx%d", dims[0], dims[1],
                        )
                        logged_resolution = True

                proc = self._proc
                if proc is None or proc.stdin is None:
                    break
                try:
                    proc.stdin.write(data)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    logger.error("ffmpeg pipe closed")
                    break
                except Exception:
                    logger.exception("Write to ffmpeg failed")
                    break

                await asyncio.sleep(self._frame_interval)
        except asyncio.CancelledError:
            logger.debug("CameraFeeder loop cancelled")
            raise

    async def stop(self) -> None:
        """Stop the feeder loop and close the ffmpeg subprocess."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                await proc.wait()
            except Exception:
                logger.exception("Error stopping ffmpeg")
            self._proc = None
        logger.info("CameraFeeder stopped")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Feed Kachaka front camera to a v4l2loopback device",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"Kachaka gRPC target (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        help=f"v4l2loopback device path (default: {DEFAULT_DEVICE})",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help=f"Frame rate in fps (default: {DEFAULT_FPS})",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = KachakaApiClient(target=args.target)
    await client.update_resolver()
    logger.info("Connected to Kachaka at %s", args.target)

    feeder = CameraFeeder(client, device=args.device, fps=args.fps)
    await feeder.start()

    # Run until interrupted (Ctrl+C or SIGTERM).
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Received stop signal, shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await stop_event.wait()
    finally:
        await feeder.stop()
        channel = getattr(client.stub, "_channel", None)
        if channel is not None:
            await channel.close()
        logger.info("Disconnected")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)