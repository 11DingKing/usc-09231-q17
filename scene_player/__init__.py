"""The scene player and its synchronisation boundary with the X service.

The player asks the X service to draw a scene and then grabs the X
framebuffer.  Drawing is asynchronous: a grab that races the draw can see
the uninitialised buffer or a half-painted frame.  The public API in this
package makes "the first finished paint" the synchronisation point and
turns every other outcome into an explicit result instead of silently
saving a blank frame.
"""

from .framebuffer import Frame, Framebuffer
from .grabber import FramebufferGrabber, GrabError
from .player import (
    CaptureResult,
    RetryExhaustedError,
    ScenePlayer,
    ScenePlayerError,
    ServiceExitedError,
    ServiceNotRunningError,
)
from .scenes import SCENES, decode_stamp, read_ppm, render, write_ppm
from .xservice import XService

__all__ = [
    "SCENES",
    "CaptureResult",
    "Frame",
    "Framebuffer",
    "FramebufferGrabber",
    "GrabError",
    "RetryExhaustedError",
    "ScenePlayer",
    "ScenePlayerError",
    "ServiceExitedError",
    "ServiceNotRunningError",
    "XService",
    "decode_stamp",
    "read_ppm",
    "render",
    "write_ppm",
]
