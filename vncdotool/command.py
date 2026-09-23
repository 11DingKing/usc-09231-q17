"""``vncdo``: play a list of scene-player commands against one X service.

    vncdo --server HOST:PORT key s pause 0.3 capture screen.png

Exit codes: 0 success, 1 usage or protocol error, 3 the X service exited
early, 4 the retry budget was exhausted.  (2 is argparse's own usage exit.)
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from vncdotool.player import (
    CaptureError,
    CapturePolicy,
    RetryExhaustedError,
    ScenePlayer,
    ServerExitedError,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SERVER_EXITED = 3
EXIT_RETRY_EXHAUSTED = 4

_COMMAND_ARITY = {"key": 1, "move": 2, "click": 1, "pause": 1, "capture": 1}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vncdo", description=__doc__)
    parser.add_argument("--server", required=True, metavar="HOST:PORT", help="X service to drive")
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=CapturePolicy.ready_timeout,
        help="seconds to wait for the first draw (default: %(default)s)",
    )
    parser.add_argument(
        "--ready-poll",
        type=float,
        default=CapturePolicy.ready_poll,
        help="seconds between readiness polls (default: %(default)s)",
    )
    parser.add_argument(
        "--capture-retries",
        type=int,
        default=CapturePolicy.capture_retries,
        help="capture attempts before giving up (default: %(default)s)",
    )
    parser.add_argument(
        "--capture-retry-delay",
        type=float,
        default=CapturePolicy.capture_retry_delay,
        help="seconds between capture attempts (default: %(default)s)",
    )
    return parser


def _usage_error(message: str) -> int:
    print(f"vncdo: {message}", file=sys.stderr)
    return EXIT_USAGE


def main(argv: Optional[List[str]] = None) -> int:
    opts, commands = build_parser().parse_known_args(argv)
    host, sep, port = opts.server.rpartition(":")
    if not sep or not host:
        return _usage_error(f"--server must be HOST:PORT, got {opts.server!r}")
    try:
        port_number = int(port)
    except ValueError:
        return _usage_error(f"--server port is not a number: {port!r}")
    policy = CapturePolicy(
        ready_timeout=opts.ready_timeout,
        ready_poll=opts.ready_poll,
        capture_retries=opts.capture_retries,
        capture_retry_delay=opts.capture_retry_delay,
    )
    player = ScenePlayer(host, port_number, policy=policy)
    try:
        player.connect()
        index = 0
        while index < len(commands):
            name = commands[index]
            arity = _COMMAND_ARITY.get(name)
            if arity is None:
                known = ", ".join(sorted(_COMMAND_ARITY))
                return _usage_error(f"unknown command {name!r}; expected one of: {known}")
            args = commands[index + 1 : index + 1 + arity]
            if len(args) < arity:
                return _usage_error(
                    f"command {name!r} needs {arity} argument(s), got {len(args)}"
                )
            index += 1 + arity
            try:
                if name == "key":
                    player.key(args[0])
                elif name == "move":
                    player.move(int(args[0]), int(args[1]))
                elif name == "click":
                    player.click(int(args[0]))
                elif name == "pause":
                    player.pause(float(args[0]))
                elif name == "capture":
                    player.capture(args[0])
            except ValueError:
                return _usage_error(f"command {name!r} got a bad argument: {args}")
    except ServerExitedError as exc:
        print(f"vncdo: {exc}", file=sys.stderr)
        return EXIT_SERVER_EXITED
    except RetryExhaustedError as exc:
        print(f"vncdo: {exc}", file=sys.stderr)
        return EXIT_RETRY_EXHAUSTED
    except CaptureError as exc:
        print(f"vncdo: {exc}", file=sys.stderr)
        return EXIT_USAGE
    finally:
        player.close()
    return EXIT_OK
