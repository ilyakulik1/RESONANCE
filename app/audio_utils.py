import os
import random

from app.constants import AUDIO_EXTENSIONS, PYDUB_AVAILABLE


def is_audio_file(file_path: str) -> bool:
    return bool(file_path) and file_path.lower().endswith(AUDIO_EXTENSIONS)


def generate_waveform_data(
    file_path=None,
    num_bars=2000,
    *,
    start_ms: int = 0,
    end_ms: int | None = None,
):
    if file_path is None:
        return []

    if not file_path or not os.path.exists(file_path):
        return []

    num_bars = max(500, num_bars)

    if not PYDUB_AVAILABLE:
        data = [random.uniform(0.1, 0.9) for _ in range(num_bars)]
        for i in range(1, len(data) - 1):
            data[i] = (data[i - 1] + data[i] + data[i + 1]) / 3
        return data

    try:
        from pydub import AudioSegment
        import numpy as np

        audio = AudioSegment.from_file(file_path)

        if audio.channels > 1:
            audio = audio.set_channels(1)

        samples = np.array(audio.get_array_of_samples(), dtype=np.float64)

        if audio.sample_width == 2:
            samples /= 32768.0
        elif audio.sample_width == 4:
            samples /= 2147483648.0
        elif audio.sample_width == 1:
            samples = (samples - 128.0) / 128.0

        if len(samples) == 0:
            return []

        if start_ms > 0 or end_ms is not None:
            frame_rate = audio.frame_rate
            start_sample = max(0, int(start_ms * frame_rate / 1000))
            if end_ms is None:
                end_sample = len(samples)
            else:
                end_sample = min(len(samples), max(start_sample + 1, int(end_ms * frame_rate / 1000)))
            samples = samples[start_sample:end_sample]

        if len(samples) == 0:
            return []

        chunk_size = max(1, len(samples) // num_bars)
        trimmed_length = chunk_size * num_bars
        samples = samples[:trimmed_length]
        chunks = samples.reshape(num_bars, chunk_size)

        peaks = np.max(np.abs(chunks), axis=1)
        peak_max = np.max(peaks)
        if peak_max > 0:
            peaks /= peak_max

        waveform_bars = peaks.tolist()

        if len(waveform_bars) > 2:
            smoothed = [waveform_bars[0]]
            for i in range(1, len(waveform_bars) - 1):
                smoothed.append((waveform_bars[i - 1] + waveform_bars[i] * 2 + waveform_bars[i + 1]) / 4)
            smoothed.append(waveform_bars[-1])
            waveform_bars = smoothed

        return [max(0.02, min(1.0, bar)) for bar in waveform_bars]

    except Exception as e:
        print(f"Error generating waveform for {file_path}: {e}")
        return [random.uniform(0.3, 0.7) for _ in range(num_bars)]
