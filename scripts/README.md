# Scripts

This folder contains helper scripts for downloading and preparing external resources used by **SpeechAug**.

## Scripts

### `download_external_resources.sh`

Downloads the full external resources used for full experiments:

- MUSAN
- OpenSLR RIRS_NOISES

```bash
bash scripts/download_external_resources.sh
```

The downloaded resources are saved under:

```text
external_resources/
├── musan/
└── RIRS_NOISES/
```

These full datasets should remain local and should not be committed to GitHub.

---

### `prepare_musan_split.py`

Prepares the full MUSAN dataset into the train/test structure expected by SpeechAug:

```text
external_resources/musan_split/
├── babble/
├── music/
└── noise/
```

Each noise type contains:

```text
train/
test/
```


For full usage details, see the main `README.md` and the files in `docs/`.
