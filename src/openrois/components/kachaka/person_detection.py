"""PersonDetection for the Kachaka.

From ``start`` to ``stop``, the component counts the persons the robot's own object
detector sees, and emits ``person_detected`` with the ``number`` of persons when the
count changes. A person is an object the detector labels as a person with a score of
``min_score`` or more. A new count is reported once it holds for ``hold`` seconds, with
the first detection that still shows it by then, so a person who flickers in and out of
detection does not flood the clients.
Every start reports the count once, so the application that starts the component learns
the count it starts from: the first count that holds after a fresh start, or at once the
count of a component that already runs. ``suspend`` holds the events back, and ``resume``
reports the count if it changed in between. A stop from any application stops the count
for all of them.

The component only reads. It long-polls the robot's detections, which takes no place in
the robot's one command slot, so it counts while Navigation drives, and the robot never
moves for it.

The robot keeps a request for a detection waiting until its detector has one, so no
detection is not a count of 0. While no detection has arrived for ``max_age`` seconds,
the component raises WARNING, on itself only, and reports no count: no event, and nothing
for a start to report again. Once detections arrive again it clears the warning and
reports the count afresh, once it holds, even if it is the count it reported before.
``stop`` clears the warning too, since a stopped component does not read. The first
answer of the robot after a start, and after each time the robot did not answer, was not
ready or refused the request, may be a detection of any age, so the component takes from
it only where to read on, and counts the detections after it.

Detections count only while the connection finds the robot up: answering and ready to
accept commands. While the robot does not answer, the connection raises ERROR, and the
count resumes once the robot is back. A wait for a detection that ends without one is not
a robot that does not answer, and raises nothing on the robot's other components.

``timestamp`` is when the adapter received the latest detection taken into the count, in
UTC: the detection that confirmed a new count, or the latest one before a count reported
again. The robot stamps each detection with its own clock, which this component does not
use, since nothing ties that clock to the clock of the adapter's machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openrois.components.core import Component, command, component, event
from openrois.interfaces.common import ComponentStatus
from openrois.interfaces.components import PERSON_DETECTION_PROFILE

from openrois.components.kachaka.connection import KachakaConnection

if TYPE_CHECKING:
    from kachaka_api.generated import kachaka_api_pb2 as pb2

__all__ = ["Debounce", "KachakaPersonDetection"]

logger = logging.getLogger(__name__)

# How long the watch waits before it asks again, at most, while the robot does not answer
# or refuses the request, in seconds.
_RETRY = 1.0


class Debounce:
    """Reports a new value once it holds for ``hold`` seconds.

    Args:
        hold: How long a value has to hold before it is reported, in seconds.
    """

    def __init__(self, hold: float) -> None:
        self._hold = hold
        self._reported: int | None = None
        self._candidate: int | None = None
        self._since = 0.0

    def update(self, value: int, now: float) -> int | None:
        """Take a new reading at time ``now``, and return a value to report, or None."""
        if value != self._candidate:
            self._candidate = value
            self._since = now
        if value != self._reported and now - self._since >= self._hold:
            self._reported = value
            return value
        return None

    def forget(self) -> None:
        """Drop the value that is waiting to hold, after readings were skipped: the
        next reading starts its hold anew."""
        self._candidate = None


@component(PERSON_DETECTION_PROFILE)
class KachakaPersonDetection(Component):
    """PersonDetection of one Kachaka: the number of persons its detector sees.

    Args:
        connection: The connection to the robot, shared with its other components.
        hold: How long a new count has to hold before it is reported, in seconds.
        min_score: The score of the robot's detector from which a person counts, from 0
            to 1.
        max_age: How long the robot may send no detection before the component raises
            WARNING and reports no count, in seconds.

    Raises:
        ValueError: ``hold`` is negative, ``min_score`` is not from 0 to 1, or
            ``max_age`` is not positive.
    """

    def __init__(
        self,
        connection: KachakaConnection,
        *,
        hold: float = 2.0,
        min_score: float = 0.5,
        max_age: float = 5.0,
    ) -> None:
        if not (math.isfinite(hold) and hold >= 0):
            raise ValueError(f"hold must be 0 or more, not {hold}")
        if not 0 <= min_score <= 1:
            raise ValueError(f"min_score must be from 0 to 1, not {min_score}")
        if not (math.isfinite(max_age) and max_age > 0):
            raise ValueError(f"max_age must be positive, not {max_age}")
        self._connection = self.uses(connection)
        self._hold = hold
        self._min_score = min_score
        self._max_age = max_age
        self._watcher: asyncio.Task[None] | None = None
        self._suspended = False
        # The count last reported since the start, which a later start reports again, and
        # when the latest detection taken into the count arrived.
        self._count: int | None = None
        self._received = datetime.now(UTC)
        # Whether the component raised WARNING for detections that do not arrive.
        self._stale = False

    async def disconnect(self) -> None:
        await self._end()

    # -- Commands --------------------------------------------------------------------------

    @command("start")
    async def start(self) -> None:
        """Start counting, and complete once the count runs. A running count goes on.

        A count that runs already is reported again, once. A fresh one is reported when
        its first value holds.
        """
        self._suspended = False
        if self._watcher is None:
            self._watcher = asyncio.create_task(self._watch(), name="kachaka-person-detection")
        elif self._count is not None:
            self._report(self._count)

    @command("stop")
    async def stop(self) -> None:
        """Stop counting. The next start reports the count again."""
        await self._end()

    @command("suspend")
    async def suspend(self) -> None:
        """Hold the events back while the detections are still read."""
        if self._watcher is not None:
            self._suspended = True

    @command("resume")
    async def resume(self) -> None:
        """Send the events again, starting with the count if it changed."""
        self._suspended = False

    @event("person_detected")
    async def person_detected(self) -> None:
        """Nothing to start: the watch emits the event when the count changes."""

    # -- The count -------------------------------------------------------------------------

    def _report(self, count: int) -> None:
        self._count = count
        # The engine sends the event to every subscription, and drops it when there is
        # none.
        self.emit("person_detected", number=count, timestamp=self._received)

    async def _end(self) -> None:
        self._suspended = False
        self._count = None
        if self._watcher is not None:
            self._watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watcher
            self._watcher = None
        if self._stale:
            self._stale = False
            self.clear_status()

    async def _watch(self) -> None:
        """Long-poll the robot's detections, and report each count that holds."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        loop = asyncio.get_running_loop()
        connection = self._connection
        pause = min(connection.retry, _RETRY)
        debounce = Debounce(self._hold)
        # 0 asks for the robot's latest detection, of any age, to read on from it.
        cursor = 0
        # When the latest detection arrived, or the wait for the first one began.
        latest = loop.time()
        refused = False
        while True:
            age = loop.time() - latest
            if age >= self._max_age and not self._stale:
                # The count is too old to report, and the next one starts afresh.
                self._stale = True
                self._count = None
                debounce = Debounce(self._hold)
                logger.warning("No detection from the Kachaka for %g s", self._max_age)
                self.raise_status(
                    ComponentStatus.WARNING,
                    f"No detection from the Kachaka for {self._max_age:g} s: the number of "
                    "persons is not known",
                )
            if not connection.is_up:
                # The connection reports the robot that does not answer or is not ready.
                cursor = 0
                debounce.forget()
                await asyncio.sleep(pause)
                continue
            # The wait ends when the count becomes too old, and is renewed after that.
            wait = self._max_age - age if age < self._max_age else self._max_age
            request = pb2.GetRequest(metadata=pb2.Metadata(cursor=cursor))
            try:
                # The wait ends here, before the deadline of the call, since a call that
                # misses its deadline counts as a robot that does not answer.
                async with asyncio.timeout(wait):
                    response: pb2.GetObjectDetectionResponse = await connection.call(
                        "GetObjectDetection", request, timeout=wait + connection.timeout
                    )
            except TimeoutError:
                continue  # The robot keeps the request waiting: it has no new detection.
            except ConnectionError:
                # The connection reports the robot that does not answer.
                cursor = 0
                debounce.forget()
                await asyncio.sleep(pause)
                continue
            except RuntimeError as exc:
                # Once per run of refusals, so a robot without the RPC does not flood the
                # log.
                if not refused:
                    logger.warning("The Kachaka gives no detection: %s", exc)
                refused = True
                cursor = 0
                debounce.forget()
                await asyncio.sleep(pause)
                continue
            refused = False
            if not connection.is_up:
                continue  # The robot stopped being up while the request waited.
            if cursor == 0:
                cursor = response.metadata.cursor
                continue
            cursor = response.metadata.cursor
            latest = loop.time()
            if self._stale:
                self._stale = False
                logger.info("The Kachaka sends detections again")
                self.clear_status()
            if self._suspended:
                # The readings wait for the resume, which reports a changed count once
                # it holds, as any change.
                debounce.forget()
                continue
            self._received = datetime.now(UTC)
            reported = debounce.update(self._persons(response), latest)
            if reported is not None:
                logger.info("The Kachaka sees %d person(s)", reported)
                self._report(reported)

    def _persons(self, detection: pb2.GetObjectDetectionResponse) -> int:
        """The number of persons in one detection: the objects labelled as a person with
        a score of ``min_score`` or more."""
        from kachaka_api.generated import kachaka_api_pb2 as pb2

        return sum(
            1
            for found in detection.objects
            if found.label == pb2.OBJECT_LABEL_PERSON and found.score >= self._min_score
        )
