"""GUI-owned GEO downloads, cache and scheduling. No writes into the core's home.

Closing the GUI cancels this coroutine and its HTTP stream. On the next launch the
applied core profile and installed timestamps are queried again, including overdue work.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import httpx

from solaris.host.base import GeoUpdateFile, GeoUpdatePlan, HostError, TunnelHost
from solaris.models.subscription import USER_AGENT

MAX_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 48 * 1024
FILES = frozenset({"GeoSite.dat", "GeoIP.dat", "geoip.metadb", "ASN.mmdb"})
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadedGeo:
    path: Path
    size: int
    sha256: str


def due(file: GeoUpdateFile, interval_hours: int, now: float) -> bool:
    return interval_hours > 0 and (
        file.updated_at_unix_ms <= 0
        or now >= file.updated_at_unix_ms / 1000 + interval_hours * 3600
    )


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class GeoDownloader:
    def __init__(
        self,
        directory: Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.transport = transport
        self.clock = clock

    def invalidate(self, file: GeoUpdateFile) -> None:
        if file.name not in FILES:
            return
        target = self.directory / file.name
        for path in (target, target.with_suffix(target.suffix + ".json")):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    async def fetch(self, file: GeoUpdateFile, max_age_s: float) -> DownloadedGeo:
        if file.name not in FILES:
            raise ValueError("unknown GEO database")
        parts = urlsplit(file.url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("GEO download URL must use HTTP or HTTPS")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / file.name
        metadata = target.with_suffix(target.suffix + ".json")
        partial = target.with_suffix(target.suffix + ".part")
        # A complete download survives a service disconnect or GUI exit. It is only a
        # cache: URL, age, size and digest must match before it can be sent again.
        try:
            saved = json.loads(metadata.read_text(encoding="utf-8"))
            if (
                saved["url"] == file.url
                and 0 <= self.clock() - saved["downloaded_at"] < max_age_s
                and target.stat().st_size == saved["size"]
                and 0 < saved["size"] <= MAX_BYTES
                and await asyncio.to_thread(_hash, target) == saved["sha256"]
            ):
                return DownloadedGeo(target, saved["size"], saved["sha256"])
        except OSError, ValueError, KeyError, TypeError:
            pass
        digest = hashlib.sha256()
        received = 0
        proxy = (
            None
            if self.transport is not None or proxy_bypass(parts.hostname)
            else getproxies().get(parts.scheme)
        )
        try:
            async with (
                asyncio.timeout(600),
                httpx.AsyncClient(
                    transport=self.transport,
                    proxy=proxy,
                    follow_redirects=True,
                    timeout=httpx.Timeout(30, connect=15),
                    headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                ) as client,
                client.stream("GET", file.url) as response,
            ):
                response.raise_for_status()
                expected = response.headers.get("Content-Length")
                size = int(expected) if expected is not None else None
                if size is not None and not 0 < size <= MAX_BYTES:
                    raise ValueError("GEO response size is out of range")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError("GEO response must not be content encoded")
                with partial.open("wb") as stream:
                    async for chunk in response.aiter_raw(CHUNK_BYTES):
                        received += len(chunk)
                        if received > MAX_BYTES:
                            raise ValueError("GEO download is too large")
                        digest.update(chunk)
                        stream.write(chunk)
                if received == 0 or (size is not None and size != received):
                    raise ValueError("GEO response is truncated or empty")
            partial.replace(target)
            record = {
                "url": file.url,
                "size": received,
                "sha256": digest.hexdigest(),
                "downloaded_at": self.clock(),
            }
            temporary = metadata.with_suffix(".json.part")
            temporary.write_text(json.dumps(record), encoding="utf-8")
            temporary.replace(metadata)
            return DownloadedGeo(target, received, digest.hexdigest())
        finally:
            partial.unlink(missing_ok=True)


class GeoUpdates:
    def __init__(
        self,
        host: TunnelHost,
        downloader: GeoDownloader,
        *,
        clock: Callable[[], float] = time.time,
        poll_s: float = 30,
        generation_check_s: float = 2,
    ) -> None:
        self.host = host
        self.downloader = downloader
        self.clock = clock
        self.poll_s = poll_s
        self.generation_check_s = generation_check_s

    async def run(self) -> None:
        retry_s = 60.0
        while True:
            delay = self.poll_s
            try:
                if self.host.core_available:
                    plan = await self.host.get_geo_update()
                    if plan.generation and plan.interval_hours > 0:
                        await self._while_current(plan)
                retry_s = 60.0
            except (HostError, httpx.HTTPError, OSError, ValueError, TimeoutError) as error:
                # The configured URL may contain credentials; do not log it in errors.
                code = error.code if isinstance(error, HostError) else type(error).__name__
                _log.warning("GEO update failed (%s); retrying in %s seconds", code, retry_s)
                delay = retry_s
                retry_s = min(3600, retry_s * 2)
            await asyncio.sleep(delay)

    async def _changed(self, generation: str) -> None:
        while True:
            await asyncio.sleep(self.generation_check_s)
            if not self.host.core_available:
                return
            if (await self.host.get_geo_update()).generation != generation:
                return

    async def _while_current(self, plan: GeoUpdatePlan) -> None:
        work = asyncio.create_task(self.update(plan))
        changed = asyncio.create_task(self._changed(plan.generation))
        try:
            done, _ = await asyncio.wait((work, changed), return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                await work
            else:
                await changed
        finally:
            work.cancel()
            changed.cancel()
            await asyncio.gather(work, changed, return_exceptions=True)

    async def update(self, plan: GeoUpdatePlan) -> None:
        for file in plan.files:
            if not due(file, plan.interval_hours, self.clock()):
                continue
            downloaded = await self.downloader.fetch(file, plan.interval_hours * 3600)
            transfer = uuid.uuid4().hex
            offset = 0
            committed = False
            try:
                with downloaded.path.open("rb") as stream:
                    while chunk := stream.read(CHUNK_BYTES):
                        await self.host.upload_geo(
                            plan.generation,
                            transfer,
                            file.name,
                            downloaded.size,
                            downloaded.sha256,
                            offset,
                            chunk,
                        )
                        offset += len(chunk)
                try:
                    await self.host.upload_geo(
                        plan.generation,
                        transfer,
                        file.name,
                        downloaded.size,
                        downloaded.sha256,
                        offset,
                        complete=True,
                    )
                except HostError as error:
                    if error.code in {"invalid_request", "operation_failed"}:
                        # A failed validation must not pin a bad source response in
                        # the cache for a whole update interval. Transport failures
                        # keep the complete download for the next attempt.
                        self.downloader.invalidate(file)
                    raise
                committed = True
                _log.info("Updated GEO database %s", file.name)
            finally:
                if not committed:
                    # Bounded cleanup; a lost connection is also covered by the core's
                    # expiry, next configuration apply, engine close and startup cleanup.
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(0.5):
                            await self.host.upload_geo(
                                plan.generation,
                                transfer,
                                file.name,
                                downloaded.size,
                                downloaded.sha256,
                                offset,
                                abort=True,
                            )
