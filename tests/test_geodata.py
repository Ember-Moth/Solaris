from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from solaris.host.base import GeoUpdateFile, GeoUpdatePlan, HostError
from solaris.host.service import ServiceHost
from solaris.host.transport import Streams
from solaris.models.geodata import CHUNK_BYTES, GeoDownloader, GeoUpdates, due
from solaris.protocol import control_pb2
from tests.support.simulated import SimulatedHost
from tests.test_control_client import info_response, serve


class Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.data


def response(data: bytes, headers: dict[str, str] | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _: httpx.Response(200, stream=Body(data), headers=headers))


def file(name: str = "GeoSite.dat", updated: int = 0) -> GeoUpdateFile:
    return GeoUpdateFile(name, "https://geo.example/data", updated)


def test_age_uses_each_enabled_database() -> None:
    assert due(file("ASN.mmdb", 0), 24, 100)
    assert not due(file("GeoIP.dat", 100_000), 24, 100)
    assert due(file("geoip.metadb", 100_000), 24, 100 + 86400)
    assert not due(file(), 0, 100)


def test_download_cache_rechecks_url_hash_and_age(tmp_path: Path) -> None:
    async def run() -> None:
        calls = []

        def answer(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return httpx.Response(200, stream=Body(b"database"))

        now = [100.0]
        downloader = GeoDownloader(
            tmp_path, transport=httpx.MockTransport(answer), clock=lambda: now[0]
        )
        first = await downloader.fetch(file(), 3600)
        assert first.sha256 == hashlib.sha256(b"database").hexdigest()
        assert (await downloader.fetch(file(), 3600)) == first
        assert len(calls) == 1
        first.path.write_bytes(b"tampered")
        await downloader.fetch(file(), 3600)
        assert len(calls) == 2
        now[0] += 3600
        await downloader.fetch(file(), 3600)
        assert len(calls) == 3
        await downloader.fetch(GeoUpdateFile(file().name, "https://geo.example/new", 0), 3600)
        assert len(calls) == 4

    asyncio.run(run())


@pytest.mark.parametrize(
    ("data", "headers"),
    [
        (b"", {}),
        (b"short", {"Content-Length": "9"}),
        (b"x", {"Content-Length": str(256 * 1024 * 1024 + 1)}),
        (b"x", {"Content-Encoding": "gzip"}),
    ],
)
def test_bad_download_keeps_installed_cache(
    tmp_path: Path, data: bytes, headers: dict[str, str]
) -> None:
    original = tmp_path / "GeoSite.dat"
    original.write_bytes(b"old")
    downloader = GeoDownloader(tmp_path, transport=response(data, headers))
    with pytest.raises(ValueError, match="GEO"):
        asyncio.run(downloader.fetch(file(), 3600))
    assert original.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_oversize_stream_and_untrusted_name_are_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("solaris.models.geodata.MAX_BYTES", 5)
    downloader = GeoDownloader(tmp_path, transport=response(b"123456"))
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(downloader.fetch(file(), 3600))
    with pytest.raises(ValueError, match="unknown"):
        asyncio.run(downloader.fetch(file("../outside"), 3600))
    assert not list(tmp_path.iterdir())


class UploadHost(SimulatedHost):
    def __init__(self, plan: GeoUpdatePlan) -> None:
        super().__init__()
        self.plan = plan
        self.generation = plan.generation  # the transfer's, even after the plan changes
        self.chunks: list[tuple[int, bytes]] = []
        self.commits = 0
        self.aborts = 0

    async def get_geo_update(self) -> GeoUpdatePlan:
        return self.plan

    async def upload_geo(
        self,
        generation: str,
        transfer_id: str,
        name: str,
        size: int,
        sha256: str,
        offset: int,
        data: bytes = b"",
        *,
        complete: bool = False,
        abort: bool = False,
    ) -> None:
        assert generation == self.generation
        assert len(transfer_id) == 32
        if abort:
            self.aborts += 1
        elif complete:
            assembled = b"".join(chunk for _, chunk in self.chunks)
            assert len(assembled) == size == offset
            assert hashlib.sha256(assembled).hexdigest() == sha256
            self.commits += 1
        else:
            assert len(data) <= CHUNK_BYTES
            assert offset == sum(len(chunk) for _, chunk in self.chunks)
            self.chunks.append((offset, data))


def test_upload_is_chunked_and_fresh_files_are_skipped(tmp_path: Path) -> None:
    plan = GeoUpdatePlan("generation", 24, (file(), file("GeoIP.dat", 100_000)))
    host = UploadHost(plan)
    data = b"a" * (CHUNK_BYTES * 2 + 19)
    updater = GeoUpdates(host, GeoDownloader(tmp_path, transport=response(data)), clock=lambda: 100)
    asyncio.run(updater.update(plan))
    assert len(host.chunks) == 3
    assert host.commits == 1
    assert host.aborts == 0


def test_gui_exit_and_profile_change_cancel_a_stalled_http_stream(tmp_path: Path) -> None:
    async def run(change_profile: bool) -> None:
        started = asyncio.Event()
        closed = asyncio.Event()

        class Stalled(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                started.set()
                await asyncio.Event().wait()
                yield b"never"

            async def aclose(self) -> None:
                closed.set()

        transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Stalled()))
        plan = GeoUpdatePlan("generation", 24, (file(),))
        host = UploadHost(plan)
        updater = GeoUpdates(
            host, GeoDownloader(tmp_path, transport=transport), generation_check_s=0.01
        )
        task = asyncio.create_task(updater._while_current(plan))
        await asyncio.wait_for(started.wait(), 1)
        if change_profile:
            host.plan = GeoUpdatePlan()
            await asyncio.wait_for(task, 1)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed.is_set()
        assert not list(tmp_path.glob("*.part"))
        assert host.commits == 0

    asyncio.run(run(False))
    asyncio.run(run(True))


class Watched(GeoUpdates):
    """Keeps the event that stops the update, so that a test can wait until it is set."""

    stale: asyncio.Event | None = None

    async def update(self, plan: GeoUpdatePlan, stale: asyncio.Event | None = None) -> None:
        self.stale = stale
        await super().update(plan, stale)


class HeldHost(UploadHost):
    """Holds the first chunk until released and notes whether that call was cancelled."""

    def __init__(self, plan: GeoUpdatePlan) -> None:
        super().__init__(plan)
        self.sending = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def upload_geo(
        self,
        generation: str,
        transfer_id: str,
        name: str,
        size: int,
        sha256: str,
        offset: int,
        data: bytes = b"",
        *,
        complete: bool = False,
        abort: bool = False,
    ) -> None:
        if not (complete or abort or self.chunks):
            self.sending.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        await super().upload_geo(
            generation,
            transfer_id,
            name,
            size,
            sha256,
            offset,
            data,
            complete=complete,
            abort=abort,
        )


@pytest.mark.parametrize("exit_gui", [False, True])
def test_profile_change_stops_an_upload_between_chunks(tmp_path: Path, exit_gui: bool) -> None:
    """A cancelled host call can close the control connection that every request shares. A
    new configuration lets the chunk in flight finish, then aborts the transfer; only the GUI
    exiting cancels the call, and leaves the transfer to the core's own cleanup."""

    async def run() -> None:
        plan = GeoUpdatePlan("generation", 24, (file(),))
        host = HeldHost(plan)
        data = b"a" * (CHUNK_BYTES * 2 + 19)
        updater = Watched(
            host, GeoDownloader(tmp_path, transport=response(data)), generation_check_s=0.01
        )
        task = asyncio.create_task(updater._while_current(plan))
        await host.sending.wait()
        if exit_gui:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert host.cancelled
            assert host.aborts == 0
            return
        host.plan = GeoUpdatePlan("next", 24, (file(),))
        assert updater.stale is not None
        await updater.stale.wait()
        host.release.set()
        await task
        assert not host.cancelled
        assert len(host.chunks) == 1
        assert (host.commits, host.aborts) == (0, 1)

    asyncio.run(run())


def test_profile_change_during_an_upload_keeps_the_service_connection(tmp_path: Path) -> None:
    """Over the control client: the core queues UploadGeo behind other serialized work, so
    its answers can be slow. Status polls and the user's own requests share the connection,
    which stays open while the transfer stops."""
    pb = control_pb2

    async def run() -> None:
        generation = "first"
        sending = asyncio.Event()
        release = asyncio.Event()
        uploads: list[control_pb2.UploadGeoRequest] = []

        async def answer(request: control_pb2.Request) -> control_pb2.Response:
            match request.WhichOneof("method"):
                case "get_geo_update":
                    files = [pb.GeoUpdateFile(name="GeoSite.dat", url="https://geo.example/data")]
                    plan = pb.GetGeoUpdateResponse(
                        generation=generation, interval_hours=24, files=files
                    )
                    return pb.Response(get_geo_update=plan)
                case "upload_geo":
                    uploads.append(request.upload_geo)
                    if request.upload_geo.abort:
                        await asyncio.sleep(0.6)  # longer than the cleanup's former bound
                    else:
                        sending.set()
                        await release.wait()
                    return pb.Response(empty=pb.Empty())
                case "get_status":
                    await release.wait()
                    return pb.Response(get_status=pb.GetStatusResponse(state=pb.RUN_STATE_RUNNING))
            return info_response()

        server, opener = await serve(answer)
        opened = 0

        async def counted() -> Streams:
            nonlocal opened
            opened += 1
            return await opener()

        data = b"a" * (CHUNK_BYTES + 1)
        async with server:
            host = ServiceHost(counted)
            updater = Watched(
                host, GeoDownloader(tmp_path, transport=response(data)), generation_check_s=0.01
            )
            try:
                plan = await host.get_geo_update()
                task = asyncio.create_task(updater._while_current(plan))
                await sending.wait()
                status = asyncio.create_task(host.get_status())
                generation = "second"
                assert updater.stale is not None
                await updater.stale.wait()
                release.set()
                await task
                assert (await status).state == "running"
                assert [upload.abort for upload in uploads] == [False, True]
                assert opened == 1
                assert host._client is not None
                assert not host._client.closed
            finally:
                await host.close()

    asyncio.run(run())


def test_opening_gui_checks_overdue_data_immediately(tmp_path: Path) -> None:
    async def run() -> None:
        plan = GeoUpdatePlan("generation", 24, (file(),))
        host = UploadHost(plan)
        updater = GeoUpdates(
            host, GeoDownloader(tmp_path, transport=response(b"data")), poll_s=3600
        )
        task = asyncio.create_task(updater.run())
        try:
            # A minute, however busy the machine, against the hour of the first poll.
            async with asyncio.timeout(updater.poll_s / 60):
                while host.commits == 0:
                    await asyncio.sleep(0.001)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert host.commits == 1

    asyncio.run(run())


@pytest.mark.parametrize("code", ["invalid_request", "connection_closed"])
def test_rejected_data_is_refetched_but_disconnect_keeps_cache(tmp_path: Path, code: str) -> None:
    class RejectingHost(UploadHost):
        async def upload_geo(self, *args, complete: bool = False, **kwargs) -> None:
            if complete:
                raise HostError(code, "rejected")
            await super().upload_geo(*args, **kwargs)

    plan = GeoUpdatePlan("generation", 24, (file(),))
    host = RejectingHost(plan)
    updater = GeoUpdates(host, GeoDownloader(tmp_path, transport=response(b"data")))
    with pytest.raises(HostError):
        asyncio.run(updater.update(plan))
    assert (tmp_path / "GeoSite.dat").exists() == (code == "connection_closed")
    assert host.aborts == 1
