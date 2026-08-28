#!/usr/bin/env python3
"""Subprocess worker: MDX-Net Inst HQ5 stem separation (isolates GIL from playback)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _emit(progress: float | None = None, **payload) -> None:
    if progress is not None:
        payload["progress"] = float(progress)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _classify(path: Path) -> str | None:
    name = path.name.lower()
    if "vocal" in name:
        return "vocals"
    if "instrument" in name or "instrum" in name:
        return "instrumental"
    return None


def _write_pcm16_wav(src: Path, dest: Path) -> None:
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(src), always_2d=True)
    data = np.asarray(data, dtype=np.float32)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data * (0.99 / peak)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write via tempfile in ASCII dir first when dest path may be non-ASCII.
    fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix="stem_out_")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        sf.write(str(tmp_path), data, int(sr), subtype="PCM_16")
        shutil.copy2(tmp_path, dest)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--vocals-out", required=True)
    parser.add_argument("--instrumental-out", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-filename", default="UVR-MDX-NET-Inst_HQ_5.onnx")
    args = parser.parse_args()

    # Limit native thread pools so air playback keeps CPU time.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

    input_path = Path(args.input)
    if not input_path.is_file():
        _emit(error=f"Input not found: {input_path}")
        return 2

    try:
        import torch

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except Exception:
        pass

    try:
        from audio_separator.separator import Separator
    except ImportError as exc:
        _emit(error=f"audio-separator not installed: {exc}")
        return 3

    # Always work in ASCII system temp — libsndfile breaks on some Unicode paths.
    work_dir = Path(tempfile.mkdtemp(prefix="sap_stems_mdx_"))
    try:
        _emit(progress=0.05)
        separator = Separator(
            log_level=40,  # ERROR only
            model_file_dir=str(args.model_dir),
            output_dir=str(work_dir),
            output_format="WAV",
            sample_rate=44100,
            use_soundfile=False,
            mdx_params={
                "hop_length": 1024,
                "segment_size": 256,
                "overlap": 0.25,
                "batch_size": 1,
                "enable_denoise": False,
            },
        )
        _emit(progress=0.15)
        separator.load_model(model_filename=args.model_filename)
        _emit(progress=0.3)

        # Copy input to ASCII temp if path is non-ASCII (safer for loaders).
        work_input = input_path
        try:
            str(input_path).encode("ascii")
        except UnicodeEncodeError:
            work_input = work_dir / f"input{input_path.suffix or '.wav'}"
            shutil.copy2(input_path, work_input)

        outputs = separator.separate(str(work_input))
        _emit(progress=0.85)

        found: dict[str, Path] = {}
        candidates = list(work_dir.iterdir())
        for item in outputs or []:
            p = Path(item)
            if not p.is_absolute():
                p = work_dir / p
            kind = _classify(p)
            if kind and p.is_file():
                found[kind] = p
        for p in candidates:
            if not p.is_file():
                continue
            kind = _classify(p)
            if kind and kind not in found:
                found[kind] = p

        if "vocals" not in found or "instrumental" not in found:
            _emit(
                error="MDX-Net did not produce vocals+instrumental: "
                + ", ".join(p.name for p in candidates if p.is_file())
            )
            return 4

        vocals_out = Path(args.vocals_out)
        instrumental_out = Path(args.instrumental_out)
        _write_pcm16_wav(found["vocals"], vocals_out)
        _write_pcm16_wav(found["instrumental"], instrumental_out)

        # Validate readable
        import soundfile as sf

        for path in (vocals_out, instrumental_out):
            info = sf.info(str(path))
            if int(info.frames) <= 0:
                _emit(error=f"Empty stem written: {path}")
                return 5

        _emit(progress=1.0, ok=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        _emit(error=str(exc))
        return 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
