# Verified signal definitions

Only signals listed under **Decoded** are extracted. Code: `src/fleetpulse/j1939/definitions.py`.

## Sources

| Source | Use | Location / hash |
|---|---|---|
| FMS-Standard Interface description v02.00 (11.11.2010), FMS-Standard Working Group (Daimler, MAN, Scania, Volvo, **Renault Trucks**, Iveco, DAF) | Signal positions, scaling, repetition rates | `docs/references/fms_document_ver02.00vers_11_11_2010.pdf`, sha256 `e3de1b50c5da9ec94ca7e75982a99e965b7a93b510c9cc4a877cde774bcb69f6`, from `http://www.fms-standard.com/down_load/` |
| University of Turku dataset record (DOI 10.23729/3160254e-85e9-4268-a636-5b3e54091706) | Confirms data was logged from the truck's **FMS interface**, 250 kbit/s, PCAN-USB + python-can, ~50 000 frames per file; no signal list or DBC provided | research.fi / Etsin |

The FMS document states that SAE J1939 prevails where the two disagree and that
unavailable data is sent "as not available according to SAE". Error and
not-available classification uses the SAE J1939-71 range conventions (below).
Neither the SAE J1939-71 document nor the J1939 Digital Annex is available in this
project, so no signal is decoded purely on the basis of J1939-71.

Unofficial copies of FMS-Standard v03–v05 were not used as authorities.

## Decoded

Byte numbers are 1-indexed; bit 1 = least significant bit. Multi-byte values are little-endian.

| PGN | Msg | SPN | Signal (column) | Position | Resolution | Offset | Unit | Operating range | FMS rate | FMS ref |
|---|---|---|---|---|---|---|---|---|---|---|
| 61444 | EEC1 | 190 | Engine speed (`engine_speed_rpm`) | byte 4–5 | 0.125 rpm/bit | 0 | rpm | 0–8031.875 | 20 ms | §1.5 p.10 |
| 61443 | EEC2 | 91 | Accelerator pedal position 1 (`accelerator_pedal_pct`) | byte 2 | 0.4 %/bit | 0 | % | 0–100 | 50 ms | §1.2 p.7 |
| 61443 | EEC2 | 92 | Engine percent load at current speed (`engine_load_pct`) | byte 3 | 1 %/bit | 0 | % | 0–125 | 50 ms | §1.2 p.7 |
| 65265 | CCVS | 84 | Wheel-based vehicle speed (`wheel_speed_kph`) | byte 2–3 | 1/256 km/h/bit | 0 | km/h | 0–250.996 | 100 ms | §1.1 p.5 |
| 65265 | CCVS | 595 | Cruise control active (`cruise_control_active`) | byte 4 bits 1–2 | 00 off, 01 on | – | state | – | 100 ms | §1.1 p.5 |
| 65265 | CCVS | 597 | Brake switch (`brake_switch`) | byte 4 bits 5–6 | 00 released, 01 depressed | – | state | – | 100 ms | §1.1 p.5 |
| 65265 | CCVS | 598 | Clutch switch (`clutch_switch`) | byte 4 bits 7–8 | 00 released, 01 depressed | – | state | – | 100 ms | §1.1 p.5, p.30 |
| 65266 | LFE | 183 | Fuel rate (`fuel_rate_lph`) | byte 1–2 | 0.05 L/h/bit | 0 | L/h | 0–3212.75 | 100 ms | §1.16 p.24 |
| 64777 | HRLFC | 5054 | High resolution engine total fuel used (`total_fuel_used_l`) | byte 5–8 | 0.001 L/bit | 0 | L | 0–4 211 081.215 | 1000 ms | §1.18 p.26 |
| 65217 | VDHR | 917 | High resolution total vehicle distance (`total_distance_m`) | byte 1–4 | 5 m/bit | 0 | m | 0–21 055 406 km | 1000 ms | §1.10 p.17 |
| 65262 | ET1 | 110 | Engine coolant temperature (`coolant_temp_c`) | byte 1 | 1 °C/bit | −40 °C | °C | −40–210 | 1000 ms | §1.13 p.21 |
| 65269 | AMB | 171 | Ambient air temperature (`ambient_temp_c`) | byte 4–5 | 0.03125 °C/bit | −273 °C | °C | −273–1734.97 | 1000 ms | §1.14 p.22 |

Signal meaning notes (from the FMS "Description acc. SAE J1939" text):

- **SPN 92** is load relative to the maximum torque available *at the current engine speed*, clipped to 0 during engine braking. It is not absolute torque or power.
- **SPN 84** is calculated from wheel or tailshaft speed and "might vary from tacho speed".
- **SPN 597** covers the service-brake foot pedal only: not the park brake, retarder or engine brake.
- **SPN 595** means cruise control is switched on. It does not guarantee the engine is under cruise control.
- **SPN 598**: FMS says trucks with an automatic gearbox send it as not available. That is what this truck does (raw `11` in 100 % of frames).
- **SPN 183 / 5054**: FMS says fuel values are "indications, not contractual values". Over the Stage 2 session the integrated SPN 183 rate (1.095 L) and the SPN 5054 counter delta (1.109 L) agree within 1.3 %. SPN 5054 has 0.001 L resolution but advances in ~0.01 L steps in this data.
- **SPN 917**: the counter delta (2.865 km) agrees with integrated SPN 84 speed (2.845 km) within 0.7 %.

### Error / not-available handling (SAE J1939-71 conventions)

| Kind | Valid | Reserved | Error | Not available |
|---|---|---|---|---|
| 1 byte | 0x00–0xFA | 0xFB–0xFD | 0xFE | 0xFF |
| 2 bytes | 0x0000–0xFAFF | 0xFB00–0xFDFF | 0xFE00–0xFEFF | 0xFF00–0xFFFF |
| 4 bytes | ≤ 0xFAFFFFFF | 0xFB000000–0xFDFFFFFF | 0xFE000000–0xFEFFFFFF | 0xFF000000–0xFFFFFFFF |
| 2-bit state | 00, 01 | – | 10 | 11 |

- **Status column:** every signal gets a `<name>_status` of `valid`, `out_of_range`, `reserved`, `error`, `not_available` or `missing_bytes`.
- **Value column:** the value is populated only when the status is `valid`.
- **`out_of_range`:** the raw value is valid but beyond the operating range given above, e.g. SPN 92 > 125 %.
- **`<name>_raw`:** the raw integer is always kept.

## Considered and not decoded

| PGN / SPN | Reason |
|---|---|
| ETC1 (61442): all SPNs, e.g. 191 output shaft speed, 161 input shaft speed, 560 driveline engaged, 574 shift in process | ETC1 is not in FMS v02.00; J1939-71 not available here |
| EBC1 (61441): all SPNs, e.g. 521 brake pedal position, 563 ABS active | EBC1 is not in FMS v02.00; J1939-71 not available here |
| EEC1 SPNs other than 190 (e.g. 512 driver's demand torque, 513 actual engine torque, 899 torque mode) | Marked "not used for FMS-Standard" in v02.00 |
| EEC2 SPNs other than 91/92 | Marked "not used for FMS-Standard" |
| CCVS SPN 596 (byte 4 bits 3–4), byte 1, byte 5, byte 6, byte 8 fields | Marked "not used for FMS-Standard" |
| CCVS SPN 976 PTO state (byte 7 bits 1–5) | Documented, but FMS gives only 3 of 32 codes and warns states differ between brands; not needed for motion analysis. Deferred |
| ETC2 (61445) SPN 523 current gear | Not in the requested PGN set and not in FMS v02.00 |
| LFE SPN 184 instantaneous fuel economy | Documented, but 100 % "not available" in this truck's data |
| DD SPN 96, HOURS SPN 247, TCO1 SPNs 1611–1624 | Documented and present; not needed for the current analysis. See `reports/part_3_20210119T100606_102050/fms_v02_coverage_matrix.csv` |

## Telemetry alignment

Code: `src/fleetpulse/telemetry/alignment.py`.

- **Grid:** 100 ms (10 Hz), the FMS rate of CCVS, the slowest verified message.
- **Value at grid time t:** the latest frame of that message with timestamp ≤ t. This is a backward as-of join: no interpolation and no look-ahead. EEC1 (50 Hz) is point-sampled, not averaged.
- **Maximum signal age:** 3 × FMS repetition period, i.e. EEC1 60 ms, EEC2 150 ms, CCVS/LFE 300 ms, HRLFC/VDHR/ET1/AMB 3 s. A cell whose latest frame is older than that stays empty; nothing is forward-filled beyond it.
- **Invalid latest frame:** if the latest frame's status is not valid, the cell is empty. It never falls back to an older valid frame.
- **Age columns:** `eec1_age_ms`, `eec2_age_ms` and `ccvs_age_ms` record the age of the frame used in each row.
- **Source address:** a message seen from more than one source address raises an error instead of being mixed.
