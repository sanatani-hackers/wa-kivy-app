"""Anti-spam / anti-runaway layer: paced outbound queue + plugin supervisor."""
import asyncio
import logging
import random
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("wa.throttle")


# --------------------------------------------------------------------------- outbound queue
@dataclass
class Job:
    chat: Any                 # neonize JID object (or anything your sender understands)
    chat_key: str             # stable string id for per-chat pacing
    text: str
    plugin: str = "core"
    quoted: Any = None        # original MessageEv if this is a reply


class OutboundQueue:
    """Single choke point for ALL outgoing messages. Plugins can never bypass it."""

    def __init__(self, send, typing=None, *, ready_check=None, min_delay=0.3, max_delay=1.0, per_char=0.015,
                 max_typing=2.0, per_chat_gap=1.5, max_per_min=30,
                 max_queued=100, max_queued_per_plugin=20):
        self._send, self._typing, self._ready_check = send, typing, ready_check
        self.min_delay, self.max_delay = min_delay, max_delay
        self.per_char, self.max_typing = per_char, max_typing
        self.per_chat_gap, self.max_per_min = per_chat_gap, max_per_min
        self.max_queued, self.max_queued_per_plugin = max_queued, max_queued_per_plugin
        self._q: asyncio.Queue = asyncio.Queue()
        self._sent = deque()                  # timestamps of recent sends (global window)
        self._last: dict[str, float] = {}     # chat_key -> last send time
        self._pending = Counter()             # plugin -> queued count
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self._task:
            self._task = asyncio.create_task(self._worker())

    def put(self, job: Job) -> bool:
        if self._q.qsize() >= self.max_queued or self._pending[job.plugin] >= self.max_queued_per_plugin:
            log.warning("queue full, dropping message from plugin %s", job.plugin)
            return False
        self._pending[job.plugin] += 1
        self._q.put_nowait(job)
        return True

    async def _worker(self):
        while True:
            job = await self._q.get()          # sleeps with zero CPU when idle
            try:
                if self._ready_check:
                    await self._ready_check()
                await self._pace(job)
                await self._send(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("send failed (plugin=%s)", job.plugin)
            finally:
                self._pending[job.plugin] -= 1

    async def _pace(self, job: Job):
        now = time.monotonic()
        while self._sent and now - self._sent[0] > 60:
            self._sent.popleft()
        if len(self._sent) >= self.max_per_min:                       # global cap
            await asyncio.sleep(60 - (now - self._sent[0]) + random.uniform(0.5, 2.0))
        gap = self._last.get(job.chat_key, 0) + self.per_chat_gap - time.monotonic()
        if gap > 0:                                                   # per-chat gap
            await asyncio.sleep(gap)
        if self._typing:
            try:
                await self._typing(job)                               # "typing..." indicator
            except Exception:
                log.debug("typing hook failed", exc_info=True)
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay)
                            + min(len(job.text) * self.per_char, self.max_typing))
        t = time.monotonic()
        self._sent.append(t)
        self._last[job.chat_key] = t


# --------------------------------------------------------------------------- plugin supervisor
@dataclass
class PluginStats:
    calls: deque = field(default_factory=deque)
    failures: int = 0
    until: float = 0.0            # quarantined until (monotonic)
    runs: int = 0
    total_time: float = 0.0
    periodic: list = field(default_factory=list)


class PluginSupervisor:
    """Runs plugin code with timeout + rate cap + failure quarantine; owns periodic tasks."""

    def __init__(self, *, handler_timeout=20.0, max_calls_per_min=30, max_failures=5,
                 quarantine_secs=300.0, min_periodic=30.0, max_periodic_per_plugin=3,
                 lag_interval=5.0, lag_warn=1.0, on_quarantine: Optional[Callable] = None,
                 on_error: Optional[Callable] = None):
        self.handler_timeout, self.max_calls_per_min = handler_timeout, max_calls_per_min
        self.max_failures, self.quarantine_secs = max_failures, quarantine_secs
        self.min_periodic, self.max_periodic = min_periodic, max_periodic_per_plugin
        self.lag_interval, self.lag_warn = lag_interval, lag_warn
        self.on_quarantine = on_quarantine
        self.on_error = on_error
        self.stats: dict[str, PluginStats] = defaultdict(PluginStats)
        self._monitor: Optional[asyncio.Task] = None

    def start_monitor(self):
        if not self._monitor:
            self._monitor = asyncio.create_task(self._lag_monitor())

    def is_quarantined(self, name: str) -> bool:
        return time.monotonic() < self.stats[name].until

    async def run(self, name: str, fn, *args):
        st, now = self.stats[name], time.monotonic()
        if now < st.until:
            return
        while st.calls and now - st.calls[0] > 60:
            st.calls.popleft()
        if len(st.calls) >= self.max_calls_per_min:
            self._quarantine(name, "call rate exceeded")
            return
        st.calls.append(now)
        try:
            await asyncio.wait_for(fn(*args), self.handler_timeout)
            st.failures = 0
        except asyncio.TimeoutError:
            self._fail(name, "timeout")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("plugin %s raised", name)
            if self.on_error:
                try:
                    self.on_error(name, repr(e))
                except Exception:
                    pass
            self._fail(name, "exception")
        finally:
            st.runs += 1
            st.total_time += time.monotonic() - now

    def _fail(self, name, why):
        st = self.stats[name]
        st.failures += 1
        log.warning("plugin %s failed (%s) %d/%d", name, why, st.failures, self.max_failures)
        if st.failures >= self.max_failures:
            self._quarantine(name, why)

    def _quarantine(self, name, why):
        st = self.stats[name]
        st.until, st.failures = time.monotonic() + self.quarantine_secs, 0
        self.cancel_plugin(name)
        log.warning("plugin %s QUARANTINED for %ds: %s", name, self.quarantine_secs, why)
        if self.on_quarantine:
            self.on_quarantine(name, why)

    def every(self, name: str, seconds: float, fn) -> bool:
        st = self.stats[name]
        if len(st.periodic) >= self.max_periodic:
            log.warning("plugin %s: too many periodic tasks", name)
            return False
        interval = max(float(seconds), self.min_periodic)             # floor => no wake-up storms

        async def loop():
            while True:
                await asyncio.sleep(interval + random.uniform(0, interval * 0.1))
                await self.run(name, fn)
        st.periodic.append(asyncio.create_task(loop()))
        return True

    def reset(self, name: str):
        """Fresh start (called when a plugin file is reloaded)."""
        st = self.stats[name]
        st.until, st.failures = 0.0, 0
        st.calls.clear()

    def cancel_plugin(self, name: str):
        st = self.stats[name]
        for t in st.periodic:
            t.cancel()
        st.periodic.clear()

    def report(self) -> dict:
        return {n: {"runs": s.runs, "secs": round(s.total_time, 2),
                    "quarantined": self.is_quarantined(n)} for n, s in self.stats.items()}

    async def _lag_monitor(self):
        """If a plugin runs blocking sync code, the loop stalls; this makes it visible."""
        while True:
            t = time.monotonic()
            await asyncio.sleep(self.lag_interval)
            lag = time.monotonic() - t - self.lag_interval
            if lag > self.lag_warn:
                log.warning("event loop blocked %.1fs: a plugin is running blocking code "
                            "(use asyncio.to_thread)", lag)
