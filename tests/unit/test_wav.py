import io
import wave

from aistudio._media.wav import pcm_to_wav


def test_pcm_to_wav_writes_a_readable_header():
    pcm = b"\x01\x00\xff\x7f" * 12000  # 24000 16-bit samples
    data = pcm_to_wav(pcm, rate=24000, channels=1)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.getnframes() == 24000
        assert w.readframes(24000) == pcm
