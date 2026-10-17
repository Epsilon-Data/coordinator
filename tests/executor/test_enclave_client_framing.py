"""Nitro EnclaveClient reply framing over VSock (socket faked).

The client sends a length-prefixed request, and the enclave (epsilon-enclave
server/server.py EnclaveServer) answers it with a 4-byte big-endian length followed
by the JSON. An unframed legacy reply (raw JSON, first byte '{') is still accepted.
"""
import json
import struct
from unittest.mock import patch

import pytest

from workers.executor.clients import EnclaveClient
from workers.executor.exceptions import EnclaveConnectionError

REPLY = {'success': True, 'public_key': '-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----\n',
         'session_id': 'session-1'}


class FakeSocket:
    """Records what is sent and replays a scripted reply, at most max_chunk bytes per recv."""

    def __init__(self, reply: bytes, max_chunk: int = 1 << 20):
        self._reply = reply
        self._max_chunk = max_chunk
        self.sent = b''
        self.received = 0
        self.closed = False

    def settimeout(self, timeout):
        pass

    def connect(self, address):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, bufsize):
        chunk = self._reply[self.received:self.received + min(bufsize, self._max_chunk)]
        self.received += len(chunk)
        return chunk

    def close(self):
        self.closed = True


def _framed(payload: bytes) -> bytes:
    return struct.pack('!I', len(payload)) + payload


@pytest.fixture
def client():
    return EnclaveClient(enclave_cid=16)


def _send(client, fake, request=None):
    with patch('workers.executor.clients.socket.socket', return_value=fake), \
            patch('workers.executor.clients.socket.AF_VSOCK', 40, create=True):
        return client._send_to_enclave(request or {'operation': 'health_check'})


class TestFramedReply:
    def test_reply_is_read_by_its_length_prefix(self, client):
        # Bytes after the declared length are not part of the reply
        fake = FakeSocket(_framed(json.dumps(REPLY).encode()) + b'trailing')

        assert _send(client, fake) == REPLY
        assert fake.received == 4 + len(json.dumps(REPLY).encode())
        assert fake.closed

    def test_reply_arriving_in_small_pieces(self, client):
        fake = FakeSocket(_framed(json.dumps(REPLY).encode()), max_chunk=3)

        assert _send(client, fake) == REPLY

    def test_large_reply(self, client):
        reply = {'success': True, 'output': 'x' * (3 * 1024 * 1024)}
        fake = FakeSocket(_framed(json.dumps(reply).encode()), max_chunk=50_000)

        assert _send(client, fake) == reply

    def test_non_ascii_reply(self, client):
        reply = {'success': True, 'output': 'naïve – 測試 🙂'}
        fake = FakeSocket(_framed(json.dumps(reply, ensure_ascii=False).encode('utf-8')))

        assert _send(client, fake) == reply

    def test_the_request_is_length_prefixed(self, client):
        request = {'operation': 'generate_rsa_keypair', 'job_id': 'JOB-1', 'key_size': 2048}
        fake = FakeSocket(_framed(json.dumps(REPLY).encode()))

        _send(client, fake, request)

        body = json.dumps(request).encode()
        assert fake.sent == struct.pack('!I', len(body)) + body

    def test_get_public_key_reads_a_framed_reply(self, client):
        fake = FakeSocket(_framed(json.dumps(REPLY).encode()))

        with patch('workers.executor.clients.socket.socket', return_value=fake), \
                patch('workers.executor.clients.socket.AF_VSOCK', 40, create=True):
            assert client.get_public_key('JOB-1') == (REPLY['public_key'], 'session-1')

    def test_framed_error_reply(self, client):
        # The enclave frames its receive-path errors (server._send_error) whatever the request
        fake = FakeSocket(_framed(json.dumps({'success': False, 'error': 'Request timeout'}).encode()))

        assert _send(client, fake) == {'success': False, 'error': 'Request timeout'}


class TestLegacyReply:
    def test_unframed_reply_is_read_to_the_end(self, client):
        fake = FakeSocket(json.dumps(REPLY).encode(), max_chunk=5)

        assert _send(client, fake) == REPLY

    def test_unframed_reply_over_the_cap_is_refused(self, client):
        with patch.object(EnclaveClient, 'MAX_RESPONSE_SIZE', 64):
            fake = FakeSocket(json.dumps({'success': True, 'output': 'x' * 100}).encode())

            with pytest.raises(MemoryError, match='exceeded max size'):
                _send(client, fake)


class TestInvalidReply:
    @pytest.mark.parametrize('length', [EnclaveClient.MAX_RESPONSE_SIZE + 1, 0x7AFFFFFF, 0])
    def test_length_over_the_cap_or_zero_is_refused_unread(self, client, length):
        fake = FakeSocket(struct.pack('!I', length) + b'{"success": true}')

        with pytest.raises(EnclaveConnectionError, match=f'Invalid enclave response length: {length}'):
            _send(client, fake)
        assert fake.received == 4

    def test_length_at_the_cap_is_accepted(self, client):
        with patch.object(EnclaveClient, 'MAX_RESPONSE_SIZE', len(json.dumps(REPLY).encode())):
            fake = FakeSocket(_framed(json.dumps(REPLY).encode()))

            assert _send(client, fake) == REPLY

    @pytest.mark.parametrize('reply', [b'', b'\x00', b'\x00\x00\x01'])
    def test_connection_closed_before_the_header(self, client, reply):
        with pytest.raises(EnclaveConnectionError, match='before response header'):
            _send(client, FakeSocket(reply))

    def test_connection_closed_mid_reply(self, client):
        payload = json.dumps(REPLY).encode()
        fake = FakeSocket(struct.pack('!I', len(payload)) + payload[:-5], max_chunk=7)

        with pytest.raises(EnclaveConnectionError, match='mid-response'):
            _send(client, fake)
        assert fake.closed
