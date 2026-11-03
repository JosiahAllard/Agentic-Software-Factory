"""kitlib.blendermcp.wire: the add-on's framing, against a server thread on a port-0 socket."""
import json
import socket
import threading
import time
import unittest

from kitlib.blendermcp import wire


class Peer:
    """A one-shot server on 127.0.0.1:0: reply(request_bytes) -> a list of byte chunks to send,
    with pause_s between them; then it closes."""

    def __init__(self, reply, pause_s=0.0):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.got = None
        self.reply, self.pause_s = reply, pause_s
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.sock.accept()
        with conn:
            buf = b''
            while b'\0' not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            self.got = buf
            for part in self.reply(buf):
                try:
                    conn.sendall(part)
                except OSError:
                    return
                time.sleep(self.pause_s)

    def close(self):
        self.sock.close()
        self.thread.join(5)


class Wire(unittest.TestCase):
    def peer(self, reply, pause_s=0.0):
        p = Peer(reply, pause_s)
        self.addCleanup(p.close)
        return p

    def test_the_request_is_one_json_object_and_a_nul(self):
        raw = wire.request('result = {"a": 1}')
        self.assertTrue(raw.endswith(b'\0'))
        self.assertEqual(raw.count(b'\0'), 1)
        self.assertEqual(json.loads(raw[:-1]), {'type': 'execute', 'code': 'result = {"a": 1}',
                                                'strict_json': True})
        with self.assertRaises(TypeError):
            wire.request(b'bytes')

    def test_a_reply_in_chunks_is_read_to_its_nul(self):
        body = json.dumps({'status': 'ok', 'result': {'x': 'y' * 5000}}).encode() + b'\0'
        p = self.peer(lambda req: [body[i:i + 700] for i in range(0, len(body), 700)], 0.01)
        reply = wire.call(p.port, 'result = {}', 5)
        self.assertEqual(reply, {'status': 'ok', 'result': {'x': 'y' * 5000}})
        self.assertEqual(json.loads(p.got[:-1])['code'], 'result = {}')

    def test_an_error_reply_comes_back_as_it_is(self):
        p = self.peer(lambda req: [b'{"status": "error", "message": "boom"}\0'])
        self.assertEqual(wire.call(p.port, 'x', 5), {'status': 'error', 'message': 'boom'})

    def test_no_whole_reply_by_the_deadline_is_a_timeout(self):
        p = self.peer(lambda req: [b'{"status"', b'', b'', b''], 1.0)
        t0 = time.monotonic()
        with self.assertRaises(wire.CallTimeout):
            wire.call(p.port, 'x', 0.5)
        self.assertLess(time.monotonic() - t0, 3.0)

    def test_a_close_before_the_nul_is_a_wire_error_not_a_timeout(self):
        p = self.peer(lambda req: [b'{"status": "ok"'])
        with self.assertRaises(wire.WireError) as cm:
            wire.call(p.port, 'x', 5)
        self.assertNotIsInstance(cm.exception, wire.CallTimeout)
        self.assertIn('closed', str(cm.exception))

    def test_a_reply_that_is_not_an_add_on_reply_is_refused(self):
        for raw in (b'not json\0', b'[1, 2]\0', b'{"status": "maybe"}\0'):
            p = self.peer(lambda req, raw=raw: [raw])
            with self.assertRaises(wire.WireError):
                wire.call(p.port, 'x', 5)

    def test_a_port_nobody_listens_on_is_refused(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(wire.WireError) as cm:
            wire.call(port, 'x', 5)
        self.assertNotIsInstance(cm.exception, wire.CallTimeout)


if __name__ == '__main__':
    unittest.main()
