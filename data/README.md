# data/

Nothing in this directory is committed except this file.

## Source dataset (obtain separately)

**CAN bus dataset collected from a heavy-duty truck**
- **Publisher:** University of Turku (contributor: TTS – Työtehoseura), 2021
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0)
- **DOI:** [10.23729/3160254e-85e9-4268-a636-5b3e54091706](https://doi.org/10.23729/3160254e-85e9-4268-a636-5b3e54091706)
- **Access:** [Etsin / Fairdata](https://etsin.fairdata.fi/dataset/7586f24f-c91b-41df-92af-283524de8b3e) · [research.fi record](https://research.fi/en/results/dataset/7586f24f-c91b-41df-92af-283524de8b3e)

The recording is from a Renault T520 6x2 (Euro VI) heavy-duty truck, logged from its FMS-Standard CAN interface (SAE J1939, 250 kbit/s). It is split into CSV files of about 50,000 frames (≈ 1 minute) each.

FleetPulse V1 uses **Part 3** only. Download the Part 3 archive and place it here unchanged:

```
data/raw/part_3.tar.xz
```

The pipelines stream members straight from the archive, so extraction is not required. Raw data is never modified.

## Derived local data (created by the pipelines, not committed)

| Path | Produced by |
|---|---|
| `data/bronze/`, `data/silver/` | development subset runs (`config/bronze_subset_part3.txt`) |
| `data/delta/` | Delta Lake runs (`--format delta`) |
| `data/discovery/` | Business Discovery V1 (`config/discovery_v1.txt`) |
| `data/gold_part3/{bronze,silver,gold}` | the Part 3 Gold build served by the API (`config/gold_part3_engine_sessions.txt`) |
| `data/processed/` | Stage 1–3 reference (pandas) pipeline outputs |
| `data/scale/` | local scale experiment |
| `data/ai_audit/` | AI Analyst audit log (JSONL) |
