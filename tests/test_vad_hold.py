"""Tests for the VAD hangover (SpeechHold) and its wiring into mic capture."""

from __future__ import annotations

from unittest import mock

from meeting_recorder.audio.vad import SpeechHold


class TestSpeechHold:
    def test_speech_writes_immediately(self):
        hold = SpeechHold(hangover_chunks=3)
        assert hold.update(True) is True

    def test_hangover_bridges_short_pause(self):
        hold = SpeechHold(hangover_chunks=3)
        assert hold.update(True) is True      # speech
        # next 3 non-speech chunks are still written (hangover)
        assert hold.update(False) is True
        assert hold.update(False) is True
        assert hold.update(False) is True
        # 4th non-speech chunk: hangover lapsed -> silence
        assert hold.update(False) is False

    def test_speech_resets_countdown(self):
        hold = SpeechHold(hangover_chunks=2)
        hold.update(True)
        hold.update(False)          # countdown 2 -> 1
        assert hold.update(True) is True   # speech again -> reset to 2
        assert hold.update(False) is True  # 2 -> 1
        assert hold.update(False) is True  # 1 -> 0
        assert hold.update(False) is False

    def test_zero_hangover_is_plain_gate(self):
        hold = SpeechHold(hangover_chunks=0)
        assert hold.update(True) is True
        assert hold.update(False) is False

    def test_long_silence_closes_gate(self):
        hold = SpeechHold(hangover_chunks=5)
        hold.update(True)
        results = [hold.update(False) for _ in range(20)]
        assert results[:5] == [True] * 5     # hangover window
        assert all(r is False for r in results[5:])  # then closed

    def test_reset_closes_immediately(self):
        hold = SpeechHold(hangover_chunks=10)
        hold.update(True)
        hold.reset()
        assert hold.update(False) is False

    def test_from_ms_rounds_to_chunks(self):
        # 300 ms hangover at 32 ms/chunk -> ~9 chunks
        hold = SpeechHold.from_ms(300.0, 32.0)
        assert hold.hangover_chunks == round(300.0 / 32.0)

    def test_from_ms_guards_zero_chunk(self):
        assert SpeechHold.from_ms(300.0, 0).hangover_chunks == 0

    def test_negative_hangover_clamped(self):
        assert SpeechHold(hangover_chunks=-5).hangover_chunks == 0


class TestMicCaptureWiring:
    def test_mic_capture_builds_hold(self):
        from meeting_recorder.audio.mic_audio import (
            MicAudioCapture,
            VAD_CHUNK_SAMPLES,
        )
        from meeting_recorder.audio.ring_buffer import RingBuffer

        cap = MicAudioCapture(
            ring_buffer=RingBuffer(max_chunks=10),
            vad=mock.MagicMock(),
            sample_rate=16000,
            vad_hangover_ms=300.0,
        )
        # 300 ms / (512/16000*1000 = 32 ms) ~= 9 chunks
        expected = round(300.0 / (VAD_CHUNK_SAMPLES / 16000 * 1000.0))
        assert cap._speech_hold.hangover_chunks == expected

    def test_zero_hangover_config(self):
        from meeting_recorder.audio.mic_audio import MicAudioCapture
        from meeting_recorder.audio.ring_buffer import RingBuffer

        cap = MicAudioCapture(
            ring_buffer=RingBuffer(max_chunks=10),
            vad=mock.MagicMock(),
            vad_hangover_ms=0.0,
        )
        assert cap._speech_hold.hangover_chunks == 0


class TestMicUnavailableWarning:
    def test_capture_loop_fires_on_error_when_no_device(self):
        # Simulate PyAudio reporting no default input device: the capture loop
        # must surface a health warning instead of silently losing the voice.
        from meeting_recorder.audio.mic_audio import MicAudioCapture
        from meeting_recorder.audio.ring_buffer import RingBuffer

        errors = []
        cap = MicAudioCapture(
            ring_buffer=RingBuffer(max_chunks=10),
            vad=mock.MagicMock(),
            on_error=lambda key: errors.append(key),
        )

        fake_pa = mock.MagicMock()
        fake_pa.PyAudio.return_value.get_default_input_device_info.side_effect = (
            OSError("No Default Input Device Available")
        )
        with mock.patch.dict(
            "sys.modules", {"pyaudiowpatch": fake_pa}
        ):
            cap._capture_loop()  # runs once, fails on device lookup, returns

        assert errors == ["mic_unavailable"]

    def test_no_error_callback_is_safe(self):
        from meeting_recorder.audio.mic_audio import MicAudioCapture
        from meeting_recorder.audio.ring_buffer import RingBuffer

        cap = MicAudioCapture(
            ring_buffer=RingBuffer(max_chunks=10), vad=mock.MagicMock()
        )
        fake_pa = mock.MagicMock()
        fake_pa.PyAudio.return_value.get_default_input_device_info.side_effect = (
            OSError("no device")
        )
        with mock.patch.dict("sys.modules", {"pyaudiowpatch": fake_pa}):
            cap._capture_loop()  # must not raise even with on_error=None
