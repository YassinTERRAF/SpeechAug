# External Resources

This document explains how external acoustic resources are expected to be organized for **SpeechAug**.

SpeechAug can use:

- MUSAN for babble, music, and noise
- OpenSLR RIRS_NOISES for room impulse responses

The full external datasets are large and should not be committed to GitHub. They should be downloaded locally and kept under ignored folders.

---

## 1. Recommended External Resource Layout

Recommended layout:

```text
external_resources/
├── musan_sample/
├── rirs_sample/
├── musan/
├── musan_split/
└── RIRS_NOISES/
```

Recommended meaning:

```text
musan_sample/  = small MUSAN-derived example subset for GitHub examples
rirs_sample/   = small RIRS-derived example subset for GitHub examples
musan/         = full raw MUSAN dataset, local only
musan_split/   = prepared full MUSAN split, local only
RIRS_NOISES/   = full RIRS_NOISES dataset, local only
```

The full datasets should be ignored by Git:

```gitignore
external_resources/musan/
external_resources/musan_split/
external_resources/RIRS_NOISES/
examples/output_*/
```

Small example subsets may be committed only if their licenses allow redistribution and attribution files are included.

---

## 2. Downloading Full Resources

Use:

```bash
bash scripts/download_external_resources.sh
```

This should download and extract resources into:

```text
external_resources/
├── musan/
└── RIRS_NOISES/
```

The full resources are used for full experiments and for preparing small example subsets.

---

## 3. MUSAN Raw Structure

Raw MUSAN commonly contains:

```text
external_resources/musan/
├── music/
├── noise/
└── speech/
```

In many installations, these folders contain subfolders such as:

```text
music/
├── fma/
├── fma-western-art/
├── hd-classical/
├── jamendo/
└── rfm/

noise/
├── free-sound/
└── sound-bible/

speech/
├── librivox/
└── us-gov/
```

Each subfolder may contain `.wav` files and metadata files such as:

```text
ANNOTATIONS
LICENSE
README
```

The preparation scripts recursively search for `.wav` files, so nested MUSAN folders are supported.

---

## 4. Preparing MUSAN for Full Experiments

SpeechAug expects noise resources in this structure:

```text
external_resources/musan_split/
├── babble/
│   ├── train/
│   └── test/
├── music/
│   ├── train/
│   └── test/
└── noise/
    ├── train/
    └── test/
```

Prepare the full split with:

```bash
python scripts/prepare_musan_split.py \
  --musan-root external_resources/musan \
  --output-root external_resources/musan_split \
  --test-ratio 0.2 \
  --sample-rate 16000 \
  --babble-duration 10.0 \
  --num-babble-train 2000 \
  --num-babble-test 500 \
  --min-babble-speakers 3 \
  --max-babble-speakers 7 \
  --seed 2025 \
  --symlink \
  --overwrite
```

Using `--symlink` is recommended on HPC systems because it avoids duplicating the original MUSAN music and noise files.

Babble files are always generated as new `.wav` files.

---

## 5. MUSAN Mapping

The preparation script maps raw MUSAN categories to SpeechAug noise categories as follows:

```text
MUSAN speech -> babble
MUSAN music  -> music
MUSAN noise  -> noise
```

AWGN is not prepared from MUSAN. It is generated synthetically by `speech_augmenter.py`.

---

## 6. Train/Test Noise Separation

The MUSAN preparation script ensures disjoint train and test noise resources:

```text
music/train and music/test use different original music clips
noise/train and noise/test use different original noise clips
babble/train and babble/test are generated from different speech source pools
```

This prevents the same noise clips from appearing in both training and testing, which is useful for evaluating generalization to unseen acoustic conditions.

---

## 7. Preparing Small MUSAN Examples for GitHub

For lightweight examples, create a small MUSAN-derived subset:

```text
external_resources/musan_sample/
├── babble/
│   ├── train/
│   └── test/
├── music/
│   ├── train/
│   └── test/
└── noise/
    ├── train/
    └── test/
```

Example command:

```bash
python scripts/prepare_musan_split_limited.py \
  --musan-root external_resources/musan \
  --output-root external_resources/musan_sample \
  --num-music-train 20 \
  --num-music-test 10 \
  --num-noise-train 20 \
  --num-noise-test 10 \
  --num-babble-train 20 \
  --num-babble-test 10 \
  --babble-duration 5.0 \
  --sample-rate 16000 \
  --min-babble-speakers 3 \
  --max-babble-speakers 7 \
  --seed 2025 \
  --overwrite
```

This creates a small example resource folder suitable for testing the code.

If this folder is committed to GitHub, keep the generated attribution file and cite the MUSAN corpus.

---

## 8. RIRS_NOISES Raw Structure

The full OpenSLR RIRS_NOISES resource is expected under:

```text
external_resources/RIRS_NOISES/
```

SpeechAug expects the simulated RIR structure to contain room categories such as:

```text
external_resources/RIRS_NOISES/
└── simulated_rirs/
    ├── smallroom/
    ├── mediumroom/
    └── largeroom/
```

Each room category can contain nested room folders:

```text
simulated_rirs/
├── smallroom/
│   ├── Room001/
│   ├── Room002/
│   └── ...
├── mediumroom/
│   ├── Room005/
│   ├── Room012/
│   └── ...
└── largeroom/
    ├── Room010/
    ├── Room021/
    └── ...
```

SpeechAug recursively searches for `.wav` files under each room category.

---

## 9. Preparing Small RIRS Examples for GitHub

For lightweight examples, create a small RIR subset:

```text
external_resources/rirs_sample/
└── simulated_rirs/
    ├── smallroom/
    ├── mediumroom/
    └── largeroom/
```

Example command:

```bash
python scripts/prepare_rirs_sample.py \
  --rirs-root external_resources/RIRS_NOISES \
  --output-root external_resources/rirs_sample \
  --rir-family simulated_rirs \
  --room-types smallroom mediumroom largeroom \
  --num-per-room 10 \
  --seed 2025 \
  --overwrite
```

This keeps the structure expected by the augmenter:

```text
rir_root / simulated_rirs / room_type / ** / *.wav
```

For example:

```text
external_resources/rirs_sample/simulated_rirs/smallroom/
external_resources/rirs_sample/simulated_rirs/mediumroom/
external_resources/rirs_sample/simulated_rirs/largeroom/
```

---

## 10. Using Small Example Resources

After preparing small examples, the augmentation tool can be run with:

```bash
python speech_augmenter.py \
  --config configs/default.yaml \
  --clean-root examples/Grid_OSD/wav \
  --manifest examples/grid_osd_manifest.txt \
  --output-root examples/output_grid_osd_default \
  --noise-root external_resources/musan_sample \
  --rir-root external_resources/rirs_sample
```

For full experiments, use:

```bash
--noise-root external_resources/musan_split
--rir-root external_resources/RIRS_NOISES
```

---

## 11. Expected Noise Root

The `--noise-root` argument should point to either:

```text
external_resources/musan_sample
```

or:

```text
external_resources/musan_split
```

The expected structure is:

```text
noise_root/
├── babble/
│   ├── train/
│   └── test/
├── music/
│   ├── train/
│   └── test/
└── noise/
    ├── train/
    └── test/
```

AWGN does not require files under the noise root.

---

## 12. Expected RIR Root

The `--rir-root` argument should point to either:

```text
external_resources/rirs_sample
```

or:

```text
external_resources/RIRS_NOISES
```

The expected structure is:

```text
rir_root/
└── simulated_rirs/
    ├── smallroom/
    ├── mediumroom/
    └── largeroom/
```

The default RIR family is:

```text
simulated_rirs
```

---

## 13. Verifying Resource Folders

Check MUSAN sample folders:

```bash
find external_resources/musan_sample -maxdepth 3 -type d | sort
```

Expected:

```text
external_resources/musan_sample/babble/train
external_resources/musan_sample/babble/test
external_resources/musan_sample/music/train
external_resources/musan_sample/music/test
external_resources/musan_sample/noise/train
external_resources/musan_sample/noise/test
```

Check RIR sample folders:

```bash
find external_resources/rirs_sample -maxdepth 4 -type d | sort
```

Expected:

```text
external_resources/rirs_sample/simulated_rirs/smallroom
external_resources/rirs_sample/simulated_rirs/mediumroom
external_resources/rirs_sample/simulated_rirs/largeroom
```

Count files:

```bash
find external_resources/musan_sample -type f -name "*.wav" | wc -l
find external_resources/rirs_sample -type f -name "*.wav" | wc -l
```

---

## 14. License and Attribution

This repository should not redistribute full external datasets.

If small MUSAN-derived or RIRS-derived examples are included in the repository:

```text
include attribution files
respect original licenses
cite the original resources
clearly state that the examples are small subsets for testing only
```

Users are responsible for verifying that any redistributed external-resource samples comply with the original dataset licenses.

---

## 15. Summary

SpeechAug uses external resources as follows:

```text
MUSAN speech -> generated babble
MUSAN music  -> music noise
MUSAN noise  -> ambient/noise
AWGN         -> generated synthetically
RIRS_NOISES  -> room impulse responses
```

Full resources are for real experiments. Small resource subsets are for GitHub examples and quick testing.
