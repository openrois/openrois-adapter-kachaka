"""Poll a Kachaka camera and write frames to a V4L2loopback device.

Run as a separate process alongside the gateway. Poll the front (or back)
camera via the Kachaka gRPC API at a fixed interval, decode the JPEG bytes,
convert to raw YUYV, and write each frame to a V4L2loopback device. A third
application (e.g. GStreamer, ffmpeg, a WebRTC pipeline) can then open the
loopback device as a regular video source and stream it over WebRTC.

Prerequisites:
    - v4l2loopback kernel module loaded with a device (e.g. /dev/video10).
    - OpenCV (cv2) for JPEG decode and YUYV conversion.
    - kachaka-api installed (pip install -e kachaka-api/python).

Usage:
    python gateway/camera_feeder.py --target 192.168.1.100:26400 --device /dev/video10
    python gateway/camera_feeder.py --target 192.168.1.100:26400 --device /dev/video10 --camera back
    python gateway/camera_feeder.py --mock --device /dev/video10

The --mock flag generates a synthetic test pattern instead of connecting to
a real robot. Useful for testing the loopback pipeline without hardware.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import struct
import sys
import time
from pathlib import Path

# Ensure the kachaka_api package is importable when running from the repo root.
_repo_root = Path(__file__).resolve().parent.parent
_kachaka_api_path = _repo_root / "kachaka-api" / "python"
if str(_kachaka_api_path) not in sys.path:
    sys.path.insert(0, str(_kachaka_api_path))

logger = logging.getLogger("camera_feeder")

# V4L2 ioctl constants for writing frames to a loopback device.
# These are the minimal definitions needed to push raw frames without
# pulling in the full v4l2 Python bindings.
VIDIOC_QUERYCAP = 0x80685600
VIDIOC_S_FMT = 0xC0CC5605
VIDIOC_S_PARM = 0xC0CC5616
V4L2_BUF_TYPE_VIDEO_OUTPUT = 0x00000002
V4L2_FIELD_NONE = 0x00000001
V4L2_PIX_FMT_YUYV = 0x56595559  # "YUYV" in little-endian
V4L2_CAP_VIDEO_OUTPUT = 0x00000002

# struct v4l2_capability is 104 bytes. We only need the capabilities field
# at offset 40 (a __u32). The rest is zeroed.
_CAP_STRUCT_SIZE = 104

# struct v4l2_format is 204 bytes (on 64-bit). The layout we care about:
#   u32 type          (offset 0)
#   union fmt         (offset 8)
#     pix: u32 width, u32 height, u32 pixelformat, u32 field, ...
# We pack the pix fields into a 200-byte union region.
_FMT_STRUCT_SIZE = 204

# struct v4l2_streamparm is 104 bytes (on 64-bit). Layout:
#   u32 type          (offset 0)
#   union parm        (offset 8)
#     output: u32 fps_numerator, u32 fps_denominator, ...
_PARM_STRUCT_SIZE = 104


def _ioctl_set_format(fd: int, width: int, height: int) -> None:
    """Set the output format on the V4L2 device to YUYV at the given size."""
    # struct v4l2_format: type + union(pix: width, height, pixelformat, field, ...)
    # Pad to _FMT_STRUCT_SIZE bytes.
    pix_fields = struct.pack(
        "=IIIIIIIIIIIIII",
        width,           # width
        height,          # height
        V4L2_PIX_FMT_YUYV,  # pixelformat
        V4L2_FIELD_NONE,    # field
        0,               # bytesperline (0 = driver default)
        0,               # sizeimage
        0,               # colorspace
        0,               # priv
        0,               # flags
        0,               # enc
        0,               # quantization
        0,               # xfer_func
        0, 0,            # padding
    )
    # The union starts at offset 8. Pad to fill the full struct.
    union_pad = b"\x00" * (192 - len(pix_fields))
    fmt_data = struct.pack("=I", V4L2_BUF_TYPE_VIDEO_OUTPUT) + b"\x00" * 4 + pix_fields + union_pad
    assert len(fmt_data) == _FMT_STRUCT_SIZE, f"format struct is {len(fmt_data)} bytes, expected {_FMT_STRUCT_SIZE}"
    fcntl.ioctl(fd, VIDIOC_S_FMT, fmt_data)

    # Set the frame rate via VIDIOC_S_PARM.
    parm_data = struct.pack(
        "=IIII",
        V4L2_BUF_TYPE_VIDEO_OUTPUT,  # type
        30,  # fps numerator (output.timeperframe.numerator)
        1,   # fps denominator (output.timeperframe.denominator)
        0,   # extendedmode
    ) + b"\x00" * (_PARM_STRUCT_SIZE - 16)
    fcntl.ioctl(fd, VIDIOC_S_PARM, parm_data)


def _open_loopback_device(device_path: str, width: int, height: int) -> int:
    """Open the V4L2loopback device and configure it for YUYV output."""
    fd = open(device_path, "rb+", buffering=0)
    try:
        # Verify it is a V4L2 device with output capability.
        cap_buf = bytearray(_CAP_STRUCT_SIZE)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, bytes(cap_buf))
        capabilities = struct.unpack_from("=I", cap_buf, 40)[0]
        if not capabilities & V4L2_CAP_VIDEO_OUTPUT:
            raise RuntimeError(
                f"{device_path} does not support video output. "
                "Is v4l2loopback loaded?"
            )
    except OSError as exc:
        fd.close()
        raise RuntimeError(
            f"Failed to query {device_path}: {exc}. "
            "Ensure the v4l2loopback module is loaded."
        ) from exc

    _ioctl_set_format(fd.fileno(), width, height)
    logger.info(
        "Opened %s as V4L2 output (%dx%d YUYV)", device_path, width, height
    )
    return fd.fileno()


def _jpeg_to_yuyv(jpeg_bytes: bytes) -> tuple[bytes, int, int]:
    """Decode JPEG bytes and convert to YUYV 4:2:2.

    Returns the raw YUYV frame and its (width, height).
    """
    import cv2
    import numpy as np

    # Decode the JPEG into a BGR numpy array.
    buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Failed to decode JPEG from camera")

    height, width = bgr.shape[:2]
    # Convert BGR to YUV, then to YUYV (Y0, U0, Y1, V0, Y2, U1, ...).
    yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
    # I420 is planar: Y plane (full), then U plane (quarter), then V plane.
    # Convert to YUYV packed format.
    y_plane = yuv[:height, :width]
    u_plane = yuv[height : height + height // 4, : width // 2]
    v_plane = yuv[height + height // 4 :, : width // 2]

    # Interleave into YUYV.
    yuyv = np.zeros((height, width, 2), dtype=np.uint8)
    yuyv[:, 0::2, 0] = y_plane[:, 0::2]  # Y0
    yuyv[:, 1::2, 0] = y_plane[:, 1::2]  # Y1
    yuyv[:, 0::2, 1] = u_plane[:, ::2].reshape(height, width // 2)  # U (shared)
    yuyv[:, 1::2, 1] = v_plane[:, ::2].reshape(height, width // 2)  # V (shared)

    return yuyv.tobytes(), width, height


def _generate_test_pattern(width: int, height: int) -> bytes:
    """Generate a synthetic YUYV test pattern for --mock mode."""
    import cv2
    import numpy as np

    # Create a moving color bar pattern.
    img = np.zeros((height, width, 3), dtype=np.uint8)
    bar_width = width // 8
    colors = [
        (255, 255, 255),  # white
        (255, 255, 0),    # yellow
        (0, 255, 255),    # cyan
        (0, 255, 0),      # green
        (255, 0, 255),    # magenta
        (255, 0, 0),      # red
        (0, 0, 255),      # blue
        (0, 0, 0),        # black
    ]
    for i, color in enumerate(colors):
        x_start = i * bar_width
        x_end = (i + 1) * bar_width if i < 7 else width
        img[:, x_start:x_end] = color

    # Add a timestamp text overlay.
    timestamp = time.strftime("%H:%M:%S")
    cv2.putText(img, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)

    # Convert to YUYV.
    yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420)
    y_plane = yuv[:height, :width]
    u_plane = yuv[height : height + height // 4, : width // 2]
    v_plane = yuv[height + height // 4 :, : width // 2]

    yuyv = np.zeros((height, width, 2), dtype=np.uint8)
    yuyv[:, 0::2, 0] = y_plane[:, 0::2]
    yuyv[:, 1::2, 0] = y_plane[:, 1::2]
    yuyv[:, 0::2, 1] = u_plane[:, ::2].reshape(height, width // 2)
    yuyv[:, 1::2, 1] = v_plane[:, ::2].reshape(height, width // 2)

    return yuyv.tobytes()


async def _poll_real_camera(
    target: str,
    camera: str,
    fd: int,
    width: int,
    height: int,
    fps: float,
) -> None:
    """Poll the Kachaka camera and write frames to the loopback device."""
    from kachaka_api.aio import KachakaApiClient

    client = KachakaApiClient(target=target)

    # Pick the right compressed-image stream based on the camera argument.
    if camera == "front":
        stream = client.front_camera_ros_compressed_image.stream()
    elif camera == "back":
        # The async client only registers front_camera_ros_compressed_image.
        # Fall back to the base method for the back camera.
        stream = _back_camera_stream(client)
    else:
        raise ValueError(f"Unknown camera: {camera}. Use 'front' or 'back'.")

    interval = 1.0 / fps
    logger.info(
        "Polling %s camera from %s at %.1f fps", camera, target, fps
    )

    try:
        async for image in stream:
            frame_start = time.monotonic()

            try:
                yuyv_data, frame_w, frame_h = _jpeg_to_yuyv(image.data)
            except ValueError as exc:
                logger.warning("Skipping frame: %s", exc)
                continue

            # If the camera resolution differs from the configured output,
            # resize before writing. This keeps the loopback format stable.
            if (frame_w, frame_h) != (width, height):
                yuyv_data = _resize_yuyv(yuyv_data, frame_w, frame_h, width, height)

            try:
                os_write(fd, yuyv_data)
            except OSError as exc:
                logger.error("Failed to write frame to device: %s", exc)
                break

            # Pace the output to the target fps.
            elapsed = time.monotonic() - frame_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
    finally:
        # The KachakaApiClient does not expose an explicit close method.
        # The gRPC channel is cleaned up when the process exits.
        pass


async def _back_camera_stream(client: object) -> typing.AsyncIterator:
    """Yield back camera compressed images by polling the base method.

    The async KachakaApiClient only registers a ResponseHandler for the
    front camera. The back camera is available via the base class method
    get_back_camera_ros_compressed_image(). We poll it in a loop.
    """
    while True:
        image = await client.get_back_camera_ros_compressed_image()
        yield image


async def _poll_mock_camera(
    fd: int,
    width: int,
    height: int,
    fps: float,
) -> None:
    """Generate synthetic frames and write them to the loopback device."""
    interval = 1.0 / fps
    logger.info("Generating mock test pattern at %.1f fps", fps)

    while True:
        frame_start = time.monotonic()

        yuyv_data = _generate_test_pattern(width, height)
        try:
            os_write(fd, yuyv_data)
        except OSError as exc:
            logger.error("Failed to write frame to device: %s", exc)
            break

        elapsed = time.monotonic() - frame_start
        sleep_time = interval - elapsed
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


def _resize_yuyv(
    data: bytes,
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
) -> bytes:
    """Resize a YUYV frame using OpenCV."""
    import cv2
    import numpy as np

    # YUYV is 2 bytes per pixel (interleaved). Reshape to (h, w, 2).
    src = np.frombuffer(data, dtype=np.uint8).reshape(src_h, src_w, 2)
    dst = cv2.resize(src, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
    return dst.tobytes()


# os.write is not imported at module level to keep the import block clean.
# This alias avoids a name collision with the standard library.
def os_write(fd: int, data: bytes) -> int:
    import os
    return os.write(fd, data)


# typing is needed for the AsyncIterator type hint in _back_camera_stream.
# Import it here so the module does not fail if typing is unused elsewhere.
import typing  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll a Kachaka camera and feed frames to a V4L2loopback device."
    )
    parser.add_argument(
        "--target",
        default="192.168.1.100:26400",
        help="Kachaka gRPC target address (default: 192.168.1.100:26400)",
    )
    parser.add_argument(
        "--device",
        default="/dev/video10",
        help="V4L2loopback device path (default: /dev/video10)",
    )
    parser.add_argument(
        "--camera",
        choices=["front", "back"],
        default="front",
        help="Which Kachaka camera to poll (default: front)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=640,
        help="Output frame width in pixels (default: 640)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=480,
        help="Output frame height in pixels (default: 480)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=10.0,
        help="Target frame rate (default: 10.0)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Generate a synthetic test pattern instead of connecting to a robot",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    fd = _open_loopback_device(args.device, args.width, args.height)

    if args.mock:
        await _poll_mock_camera(fd, args.width, args.height, args.fps)
    else:
        await _poll_real_camera(
            target=args.target,
            camera=args.camera,
            fd=fd,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )


if __name__ == "__main__":
    asyncio.run(main())