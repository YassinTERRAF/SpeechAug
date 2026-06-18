#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
general_speech_augmenter.py

Task-independent speech dataset augmentation tool.

This tool generates controlled acoustic variants of existing speech datasets:
  1) noisy-only speech
  2) reverberant-only speech
  3) noisy-reverberant speech

It is independent of the downstream task. It can be used for:
  - speaker identification / verification
  - overlapping speech detection
  - speech recognition
  - diarization
  - speech emotion recognition
  - VAD / OSD / enhancement robustness evaluation
  - any other task where acoustic robustness matters

Noise sources:
  - babble, music, and ambient/noise are loaded from a noise corpus such as MUSAN.
  - AWGN is generated synthetically using Gaussian white noise.

RIR sources:
  - Room impulse responses are loaded from an RIR database such as OpenSLR RIRS_NOISES.
  - Room conditions can be organized as smallroom, mediumroom, largeroom, or any
    custom folder names.

Expected input manifest:
  A text file where each line contains a relative path to a clean wav file, for example:

      spk1/session1/utt001.wav
      spk1/session1/utt002.wav
      spk2/session3/utt001.wav

  Optional split format is also supported:

      train spk1/session1/utt001.wav
      valid spk1/session1/utt002.wav
      test  spk2/session3/utt001.wav

  If a split is not provided, all files are assigned to "all".

Expected clean root:
  clean_root / relative_path

Expected non-AWGN noise layout:
  noise_root / noise_type / split / *.wav

Example:
  noise_root/babble/train/*.wav
  noise_root/music/train/*.wav
  noise_root/noise/train/*.wav
  noise_root/babble/test/*.wav

Expected RIR layout:
  rir_root / rir_family / room_type / ** / *.wav

Example:
  RIRS_NOISES/simulated_rirs/smallroom/room1/*.wav
  RIRS_NOISES/simulated_rirs/mediumroom/room2/*.wav
  RIRS_NOISES/simulated_rirs/largeroom/room3/*.wav

Main outputs:
  output_root/noisy/<noise_type>/<snr>dB/<relative_path>
  output_root/reverb/<room_type>/<relative_path>
  output_root/noisy_reverb/<room_type>/<noise_type>/<snr>dB/<relative_path>

Author: prepared for a general reusable GitHub augmentation repo.
"""

import argparse
import hashlib
import json
import math
import os
import random
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import librosa
import numpy as np
import soundfile as sf
import yaml
from scipy.signal import fftconvolve
from tqdm import tqdm
from joblib import Parallel, delayed


EPS = 1e-12


# -------------------------------------------------------------------------
# Reproducibility
# -------------------------------------------------------------------------

def stable_seed(text: str, base_seed: int = 2025) -> int:
    """
    Stable seed independent of Python's randomized hash().
    """
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    return (base_seed ^ int(digest[:8], 16)) & 0xFFFFFFFF


def get_rng(key: str, base_seed: int = 2025) -> np.random.Generator:
    return np.random.default_rng(stable_seed(key, base_seed))


# -------------------------------------------------------------------------
# Audio helpers
# -------------------------------------------------------------------------

def load_audio(path: Path, sr: int, mono: bool = True) -> np.ndarray:
    audio, _ = librosa.load(str(path), sr=sr, mono=mono)
    return audio.astype(np.float32, copy=False)


def peak(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.max(np.abs(x)))


def protect_from_clipping(x: np.ndarray, max_peak: float = 0.999) -> np.ndarray:
    p = peak(x)
    if p > max_peak:
        x = x * (max_peak / (p + EPS))
    return x.astype(np.float32, copy=False)


def match_length(audio: np.ndarray, target_len: int, rng: np.random.Generator) -> np.ndarray:
    """
    Repeat or crop audio to target_len.
    """
    if len(audio) == target_len:
        return audio

    if len(audio) < target_len:
        reps = int(math.ceil(target_len / max(len(audio), 1)))
        return np.tile(audio, reps)[:target_len].astype(np.float32, copy=False)

    start = int(rng.integers(0, len(audio) - target_len + 1))
    return audio[start:start + target_len].astype(np.float32, copy=False)


def rms_power(x: np.ndarray) -> float:
    return float(np.mean(np.square(x))) + EPS


def scale_noise_to_snr(reference: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """
    Scale noise relative to reference signal to obtain target SNR in dB.

    SNR = 10 log10(P_reference / P_noise_scaled)
    """
    ref_power = rms_power(reference)
    noise_power = rms_power(noise)
    snr_linear = 10.0 ** (snr_db / 10.0)
    scale = math.sqrt(ref_power / (noise_power * snr_linear + EPS))
    return (noise * scale).astype(np.float32, copy=False)


def generate_awgn(length: int, rng: np.random.Generator) -> np.ndarray:
    """
    Generate additive Gaussian white noise. This is synthetic and does not come from MUSAN.
    """
    return rng.normal(loc=0.0, scale=1.0, size=length).astype(np.float32)


# -------------------------------------------------------------------------
# RIR helpers
# -------------------------------------------------------------------------

def align_rir_to_direct_path(
    rir: np.ndarray,
    sr: int,
    threshold_fraction: float = 0.2,
    preroll_ms: float = 1.0,
) -> np.ndarray:
    """
    Align RIR so convolution starts close to the direct path.
    """
    if rir.size == 0:
        return rir

    rir = rir.astype(np.float32, copy=False)
    rir = rir - np.mean(rir)

    abs_r = np.abs(rir)
    mx = float(abs_r.max())
    if mx < EPS:
        return rir

    threshold = mx * threshold_fraction
    idx = int(np.argmax(abs_r >= threshold))

    if abs_r[idx] < threshold:
        idx = int(np.argmax(abs_r))

    preroll = int(round(sr * preroll_ms / 1000.0))
    start = max(0, idx - preroll)

    if rir[start:].size >= 8:
        return rir[start:].astype(np.float32, copy=False)
    return rir.astype(np.float32, copy=False)


def convolve_with_rir(
    audio: np.ndarray,
    rir: np.ndarray,
    sr: int,
    keep_length: bool = True,
    normalize_rir_peak: bool = False,
    normalize_output_rms_to_input: bool = False,
    direct_threshold_fraction: float = 0.2,
    preroll_ms: float = 1.0,
) -> np.ndarray:
    """
    Apply room reverberation by FFT convolution.
    """
    rir_aligned = align_rir_to_direct_path(
        rir,
        sr=sr,
        threshold_fraction=direct_threshold_fraction,
        preroll_ms=preroll_ms,
    )

    if normalize_rir_peak:
        p = peak(rir_aligned)
        if p > EPS:
            rir_aligned = rir_aligned / p

    y = fftconvolve(audio, rir_aligned, mode="full")

    if keep_length:
        y = y[:len(audio)]

    y = y.astype(np.float32, copy=False)

    if normalize_output_rms_to_input:
        in_rms = math.sqrt(rms_power(audio))
        out_rms = math.sqrt(rms_power(y))
        y = y * (in_rms / (out_rms + EPS))

    return protect_from_clipping(y)


# -------------------------------------------------------------------------
# RIR indexing and optional RT60/DRR metadata
# -------------------------------------------------------------------------

@dataclass
class RIRItem:
    path: Path
    room_type: str
    rt60: Optional[float] = None
    drr: Optional[float] = None


def list_rirs(rir_root: Path, rir_family: str, room_types: List[str]) -> Dict[str, List[Path]]:
    """
    List RIR wav files for each room type.
    """
    result: Dict[str, List[Path]] = {}
    for room_type in room_types:
        pattern = str(rir_root / rir_family / room_type / "**" / "*.wav")
        paths = sorted(Path(p) for p in glob.glob(pattern, recursive=True))
        if not paths:
            raise FileNotFoundError(f"No RIR wav files found for room_type={room_type}: {pattern}")
        result[room_type] = paths
    return result


def choose_rir(
    rir_index: Dict[str, List[Path]],
    room_type: str,
    rng: np.random.Generator,
) -> Path:
    paths = rir_index[room_type]
    idx = int(rng.integers(0, len(paths)))
    return paths[idx]


def choose_two_different_rirs_same_room(
    rir_index: Dict[str, List[Path]],
    room_type: str,
    rng: np.random.Generator,
) -> Tuple[Path, Path]:
    """
    Select two RIRs from the same room category:
      - one for speech
      - one for environmental noise

    If only one RIR exists, it is reused.
    """
    paths = rir_index[room_type]
    if len(paths) == 1:
        return paths[0], paths[0]

    i = int(rng.integers(0, len(paths)))
    j = int(rng.integers(0, len(paths) - 1))
    if j >= i:
        j += 1
    return paths[i], paths[j]


# -------------------------------------------------------------------------
# Noise indexing
# -------------------------------------------------------------------------

def list_noise_files(noise_root: Path, noise_type: str, split: str) -> List[Path]:
    """
    List non-AWGN noise files.

    Supports:
      noise_root/noise_type/split/*.wav

    If the requested split does not exist, the function falls back to:
      noise_root/noise_type/*.wav
    """
    split_dir = noise_root / noise_type / split
    if split_dir.exists():
        files = sorted(split_dir.glob("*.wav"))
    else:
        files = sorted((noise_root / noise_type).glob("*.wav"))

    if not files:
        raise FileNotFoundError(
            f"No noise wav files found for noise_type={noise_type}, split={split}. "
            f"Expected {split_dir}/*.wav or {(noise_root / noise_type)}/*.wav"
        )

    return files


def choose_noise_segment(
    noise_root: Path,
    noise_type: str,
    split: str,
    target_len: int,
    sr: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Return a noise segment of target_len.

    For AWGN, generate synthetic white noise.
    For other types, load from a noise corpus such as MUSAN.
    """
    if noise_type.lower() in {"awgn", "white", "gaussian", "white_noise"}:
        return generate_awgn(target_len, rng)

    files = list_noise_files(noise_root, noise_type, split)
    noise_path = files[int(rng.integers(0, len(files)))]
    noise = load_audio(noise_path, sr=sr, mono=True)
    return match_length(noise, target_len, rng)


# -------------------------------------------------------------------------
# Manifest
# -------------------------------------------------------------------------

@dataclass
class ManifestItem:
    split: str
    rel_path: Path


def read_manifest(manifest_path: Path) -> List[ManifestItem]:
    """
    Read a manifest.

    Supported formats:
      rel/path.wav
      split rel/path.wav

    Lines beginning with # are ignored.
    """
    items: List[ManifestItem] = []

    with manifest_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()

            if len(parts) == 1:
                split = "all"
                rel_path = parts[0]
            elif len(parts) >= 2:
                split = parts[0]
                rel_path = parts[1]
            else:
                raise ValueError(f"Invalid manifest line {line_no}: {line}")

            items.append(ManifestItem(split=split, rel_path=Path(rel_path)))

    if not items:
        raise ValueError(f"Manifest is empty: {manifest_path}")

    return items


def map_split_for_noise(split: str) -> str:
    """
    Map dataset split to noise split.

    This default keeps train/valid separated from test noise:
      train -> train
      valid/dev/val -> train
      test/eval -> test
      all -> train

    You can change this policy depending on your corpus split.
    """
    split_lower = split.lower()
    if split_lower in {"test", "eval", "evaluation", "3"}:
        return "test"
    return "train"


# -------------------------------------------------------------------------
# Augmentation core
# -------------------------------------------------------------------------

def make_output_path(
    output_root: Path,
    condition: str,
    rel_path: Path,
    noise_type: Optional[str] = None,
    snr_db: Optional[float] = None,
    room_type: Optional[str] = None,
) -> Path:
    parts = [output_root, condition]

    if room_type is not None:
        parts.append(Path(room_type))

    if noise_type is not None:
        parts.append(Path(noise_type))

    if snr_db is not None:
        snr_str = f"{snr_db:g}dB"
        parts.append(Path(snr_str))

    parts.append(rel_path)

    out_path = parts[0]
    for p in parts[1:]:
        out_path = out_path / p

    return out_path


def write_audio(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio.astype(np.float32, copy=False), sr)


def augment_noisy_only(
    clean: np.ndarray,
    rel_path: Path,
    split: str,
    args,
) -> List[Path]:
    """
    Generate noisy-only variants.
    """
    written: List[Path] = []

    for noise_type in args.noise_types:
        for snr_db in args.snr_levels:
            rng = get_rng(f"noisy|{split}|{rel_path}|{noise_type}|{snr_db}", args.seed)
            noise_split = map_split_for_noise(split)

            noise = choose_noise_segment(
                args.noise_root,
                noise_type,
                noise_split,
                target_len=len(clean),
                sr=args.sr,
                rng=rng,
            )

            noise_scaled = scale_noise_to_snr(clean, noise, snr_db)
            mixture = protect_from_clipping(clean + noise_scaled)

            out_path = make_output_path(
                args.output_root,
                condition="noisy",
                rel_path=rel_path,
                noise_type=noise_type,
                snr_db=snr_db,
            )
            write_audio(out_path, mixture, args.sr)
            written.append(out_path)

    return written


def augment_reverb_only(
    clean: np.ndarray,
    rel_path: Path,
    split: str,
    rir_index: Dict[str, List[Path]],
    args,
) -> List[Path]:
    """
    Generate reverberant-only variants.
    """
    written: List[Path] = []

    for room_type in args.room_types:
        rng = get_rng(f"reverb|{split}|{rel_path}|{room_type}", args.seed)
        rir_path = choose_rir(rir_index, room_type, rng)
        rir = load_audio(rir_path, sr=args.sr, mono=True)

        y = convolve_with_rir(
            clean,
            rir,
            sr=args.sr,
            keep_length=args.keep_length,
            normalize_rir_peak=args.normalize_rir_peak,
            normalize_output_rms_to_input=args.normalize_reverb_rms,
            direct_threshold_fraction=args.direct_threshold_fraction,
            preroll_ms=args.preroll_ms,
        )

        out_path = make_output_path(
            args.output_root,
            condition="reverb",
            rel_path=rel_path,
            room_type=room_type,
        )
        write_audio(out_path, y, args.sr)
        written.append(out_path)

    return written


def augment_noisy_reverb(
    clean: np.ndarray,
    rel_path: Path,
    split: str,
    rir_index: Dict[str, List[Path]],
    args,
) -> List[Path]:
    """
    Generate noisy-reverberant variants.

    For environmental noise types:
      speech_reverb = clean speech convolved with speech RIR
      noise_reverb  = noise convolved with a different noise RIR from the same room
      mixture       = speech_reverb + scaled(noise_reverb)

    For AWGN:
      speech_reverb = clean speech convolved with speech RIR
      awgn          = synthetic Gaussian white noise, not reverberated
      mixture       = speech_reverb + scaled(awgn)
    """
    written: List[Path] = []

    for room_type in args.room_types:
        for noise_type in args.noise_types:
            for snr_db in args.snr_levels:
                rng = get_rng(
                    f"noisy_reverb|{split}|{rel_path}|{room_type}|{noise_type}|{snr_db}",
                    args.seed,
                )
                noise_split = map_split_for_noise(split)

                speech_rir_path, noise_rir_path = choose_two_different_rirs_same_room(
                    rir_index,
                    room_type,
                    rng,
                )

                speech_rir = load_audio(speech_rir_path, sr=args.sr, mono=True)

                speech_reverb = convolve_with_rir(
                    clean,
                    speech_rir,
                    sr=args.sr,
                    keep_length=args.keep_length,
                    normalize_rir_peak=args.normalize_rir_peak,
                    normalize_output_rms_to_input=args.normalize_reverb_rms,
                    direct_threshold_fraction=args.direct_threshold_fraction,
                    preroll_ms=args.preroll_ms,
                )

                raw_noise = choose_noise_segment(
                    args.noise_root,
                    noise_type,
                    noise_split,
                    target_len=len(clean),
                    sr=args.sr,
                    rng=rng,
                )

                is_awgn = noise_type.lower() in {"awgn", "white", "gaussian", "white_noise"}

                if is_awgn:
                    # AWGN models channel/sensor noise, so it is added directly.
                    noise_for_mix = raw_noise
                else:
                    # Environmental noise is reverberated using a separate RIR
                    # from the same room category.
                    noise_rir = load_audio(noise_rir_path, sr=args.sr, mono=True)
                    noise_for_mix = convolve_with_rir(
                        raw_noise,
                        noise_rir,
                        sr=args.sr,
                        keep_length=True,
                        normalize_rir_peak=args.normalize_rir_peak,
                        normalize_output_rms_to_input=False,
                        direct_threshold_fraction=args.direct_threshold_fraction,
                        preroll_ms=args.preroll_ms,
                    )

                noise_for_mix = match_length(noise_for_mix, len(speech_reverb), rng)

                # For noisy-reverberant speech, SNR is computed relative to the
                # reverberated speech, not the original clean speech.
                noise_scaled = scale_noise_to_snr(speech_reverb, noise_for_mix, snr_db)
                mixture = protect_from_clipping(speech_reverb + noise_scaled)

                out_path = make_output_path(
                    args.output_root,
                    condition="noisy_reverb",
                    rel_path=rel_path,
                    room_type=room_type,
                    noise_type=noise_type,
                    snr_db=snr_db,
                )
                write_audio(out_path, mixture, args.sr)
                written.append(out_path)

    return written


def process_item(item: ManifestItem, rir_index: Dict[str, List[Path]], args) -> Dict[str, int]:
    """
    Process one clean utterance.
    """
    clean_path = args.clean_root / item.rel_path

    if not clean_path.exists():
        raise FileNotFoundError(f"Clean wav not found: {clean_path}")

    clean = load_audio(clean_path, sr=args.sr, mono=True)

    counts = {
        "noisy": 0,
        "reverb": 0,
        "noisy_reverb": 0,
    }

    if args.make_noisy:
        counts["noisy"] = len(augment_noisy_only(clean, item.rel_path, item.split, args))

    if args.make_reverb:
        counts["reverb"] = len(augment_reverb_only(clean, item.rel_path, item.split, rir_index, args))

    if args.make_noisy_reverb:
        counts["noisy_reverb"] = len(augment_noisy_reverb(clean, item.rel_path, item.split, rir_index, args))

    return counts



# -------------------------------------------------------------------------
# Configuration helper
# -------------------------------------------------------------------------

def apply_config_to_args(args):
    """
    Apply YAML config values to argparse namespace.

    Dataset paths are intentionally passed from the command line:
      --clean-root, --manifest, --output-root, --noise-root, --rir-root

    The config controls augmentation protocol:
      sample_rate, seed, snr_levels, noise_types, room_types, rir_family,
      conditions, keep_length, normalization, direct-path alignment.
    """
    if args.config is None:
        return args

    if not args.config.exists():
        raise FileNotFoundError(f"Config file not found: {args.config}")

    with args.config.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if "sample_rate" in cfg:
        args.sr = int(cfg["sample_rate"])

    if "seed" in cfg:
        args.seed = int(cfg["seed"])

    if "snr_levels" in cfg:
        args.snr_levels = [float(x) for x in cfg["snr_levels"]]

    if "noise_types" in cfg:
        args.noise_types = list(cfg["noise_types"])

    if "room_types" in cfg:
        args.room_types = list(cfg["room_types"])

    if "rir_family" in cfg:
        args.rir_family = str(cfg["rir_family"])

    if "keep_length" in cfg:
        args.keep_length = bool(cfg["keep_length"])

    if "normalize_rir_peak" in cfg:
        args.normalize_rir_peak = bool(cfg["normalize_rir_peak"])

    if "normalize_reverb_rms" in cfg:
        args.normalize_reverb_rms = bool(cfg["normalize_reverb_rms"])

    if "direct_threshold_fraction" in cfg:
        args.direct_threshold_fraction = float(cfg["direct_threshold_fraction"])

    if "preroll_ms" in cfg:
        args.preroll_ms = float(cfg["preroll_ms"])

    conditions = cfg.get("conditions", {})
    if conditions:
        args.make_noisy = bool(conditions.get("noisy", False))
        args.make_reverb = bool(conditions.get("reverb", False))
        args.make_noisy_reverb = bool(conditions.get("noisy_reverb", False))

    return args


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Task-independent speech augmentation tool for noisy, reverberant, and noisy-reverberant conditions."
    )

    parser.add_argument("--config", type=Path, default=None,
                        help="Optional YAML configuration file. CLI arguments provide dataset paths.")

    parser.add_argument("--clean-root", type=Path, required=True,
                        help="Root directory containing clean wav files.")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Manifest file with relative wav paths, optionally preceded by split.")
    parser.add_argument("--output-root", type=Path, required=True,
                        help="Output root directory.")
    parser.add_argument("--noise-root", type=Path, required=True,
                        help="Root directory for non-AWGN noise files, e.g., MUSAN split.")
    parser.add_argument("--rir-root", type=Path, required=True,
                        help="Root directory for RIR database, e.g., RIRS_NOISES.")
    parser.add_argument("--rir-family", type=str, default="simulated_rirs",
                        help="RIR family under rir_root, e.g., simulated_rirs or real_rirs_isotropic_noises.")

    parser.add_argument("--sr", type=int, default=16000,
                        help="Target sampling rate.")
    parser.add_argument("--snr-levels", type=float, nargs="+",
                        default=[-5, 0, 5, 10, 15, 20],
                        help="SNR levels in dB.")
    parser.add_argument("--noise-types", type=str, nargs="+",
                        default=["babble", "music", "noise", "awgn"],
                        help="Noise types. Use awgn for synthetic Gaussian white noise.")
    parser.add_argument("--room-types", type=str, nargs="+",
                        default=["smallroom", "mediumroom", "largeroom"],
                        help="Room/RIR condition names.")

    parser.add_argument("--make-noisy", action="store_true",
                        help="Generate noisy-only files.")
    parser.add_argument("--make-reverb", action="store_true",
                        help="Generate reverberant-only files.")
    parser.add_argument("--make-noisy-reverb", action="store_true",
                        help="Generate noisy-reverberant files.")

    parser.add_argument("--all-conditions", action="store_true",
                        help="Generate noisy-only, reverb-only, and noisy-reverberant files.")

    parser.add_argument("--keep-length", action="store_true", default=True,
                        help="Keep output length equal to clean utterance length.")
    parser.add_argument("--no-keep-length", dest="keep_length", action="store_false",
                        help="Keep full convolution tail for reverberant outputs.")

    parser.add_argument("--normalize-rir-peak", action="store_true",
                        help="Peak-normalize RIR before convolution.")
    parser.add_argument("--normalize-reverb-rms", action="store_true",
                        help="Normalize reverberated speech RMS to clean speech RMS.")

    parser.add_argument("--direct-threshold-fraction", type=float, default=0.2,
                        help="Threshold fraction for RIR direct-path alignment.")
    parser.add_argument("--preroll-ms", type=float, default=1.0,
                        help="Pre-roll kept before detected RIR direct path.")

    parser.add_argument("--seed", type=int, default=2025,
                        help="Global random seed.")
    parser.add_argument("--num-jobs", type=int, default=-1,
                        help="Number of parallel jobs.")

    return parser.parse_args()


def main():
    args = parse_args()
    args = apply_config_to_args(args)

    if args.all_conditions:
        args.make_noisy = True
        args.make_reverb = True
        args.make_noisy_reverb = True

    if not (args.make_noisy or args.make_reverb or args.make_noisy_reverb):
        raise ValueError(
            "No augmentation condition selected. Use --all-conditions or one of "
            "--make-noisy, --make-reverb, --make-noisy-reverb."
        )

    items = read_manifest(args.manifest)
    rir_index = list_rirs(args.rir_root, args.rir_family, args.room_types)

    print("Configuration")
    print("-------------")
    print(f"Clean root       : {args.clean_root}")
    print(f"Output root      : {args.output_root}")
    print(f"Noise root       : {args.noise_root}")
    print(f"RIR root         : {args.rir_root}")
    print(f"RIR family       : {args.rir_family}")
    print(f"Sample rate      : {args.sr}")
    print(f"SNR levels       : {args.snr_levels}")
    print(f"Noise types      : {args.noise_types}")
    print(f"Room types       : {args.room_types}")
    print(f"Manifest items   : {len(items)}")
    print(f"Generate noisy   : {args.make_noisy}")
    print(f"Generate reverb  : {args.make_reverb}")
    print(f"Generate n+r     : {args.make_noisy_reverb}")
    print("")

    results = Parallel(n_jobs=args.num_jobs)(
        delayed(process_item)(item, rir_index, args)
        for item in tqdm(items, total=len(items), desc="Augmenting")
    )

    total_noisy = sum(r["noisy"] for r in results)
    total_reverb = sum(r["reverb"] for r in results)
    total_noisy_reverb = sum(r["noisy_reverb"] for r in results)

    print("")
    print("Done.")
    print(f"Noisy files           : {total_noisy}")
    print(f"Reverberant files     : {total_reverb}")
    print(f"Noisy-reverberant     : {total_noisy_reverb}")


if __name__ == "__main__":
    main()

