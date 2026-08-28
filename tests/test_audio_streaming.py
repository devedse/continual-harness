import asyncio
import json

import numpy as np

from pokemon_red_env.red_emulator import RedEmulator
from server.app import AudioConnectionManager


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.text_messages = []
        self.byte_messages = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, message):
        self.text_messages.append(json.loads(message))

    async def send_bytes(self, message):
        self.byte_messages.append(message)


def test_audio_manager_sends_config_rate_and_pcm():
    async def exercise():
        manager = AudioConnectionManager()
        websocket = FakeWebSocket()

        await manager.connect(websocket)
        manager.publish(b"\x01\x02\x03\x04", 4 / 3)
        assert await manager.send_next(websocket) is True

        assert websocket.accepted is True
        assert websocket.text_messages[0] == {
            "type": "audio-config",
            "sample_rate": 48000,
            "channels": 2,
            "format": "s16le",
        }
        assert websocket.text_messages[1]["type"] == "audio-rate"
        assert websocket.text_messages[1]["playback_rate"] == 4 / 3
        assert websocket.byte_messages == [b"\x01\x02\x03\x04"]

    asyncio.run(exercise())


def test_red_audio_is_expanded_to_signed_16_bit_pcm():
    emulator = RedEmulator.__new__(RedEmulator)
    emulator.pyboy = type(
        "FakePyBoy",
        (),
        {"sound": type("FakeSound", (), {"ndarray": np.array([[-128, 127], [0, -1]], dtype=np.int8)})()},
    )()

    pcm = np.frombuffer(emulator.consume_audio(), dtype="<i2").reshape(-1, 2)
    np.testing.assert_array_equal(pcm, [[-32768, 32512], [0, -256]])
