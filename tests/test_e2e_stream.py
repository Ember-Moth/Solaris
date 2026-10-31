"""The E2E download helper recognizes HTTP completion, not just socket EOF."""

import socketserver
import threading

import pytest

from tests.e2e.app import Stream


@pytest.mark.parametrize("truncated", [False, True])
def test_stream_checks_content_length_even_when_the_connection_stays_open(truncated: bool) -> None:
    release = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                pass
            length = 5 if truncated else 3
            headers = (
                f"HTTP/1.1 200 OK\r\nContent-Length: {length}\r\nConnection: keep-alive\r\n\r\n"
            )
            self.wfile.write(headers.encode() + b"abc")
            self.wfile.flush()
            if not truncated:
                release.wait(3)

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            stream = Stream(port, f"http://127.0.0.1:{port}/")
            assert stream.done.wait(1)
            assert stream.received == 3
            assert bool(stream.error) is truncated
        finally:
            release.set()
            server.shutdown()
            thread.join()
