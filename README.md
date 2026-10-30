# Maestro converter

Converts MIDI or MusicXML into Maestro (GW2) song JSON.

## Setup

- **MIDI input:** nothing to install (Python 3.8+).
- **MusicXML input (.mxl / .musicxml / .xml):** `pip install music21`

## Usage

```bash
# 1. See what's inside (piano scores usually have one track/part per hand)
python maestro_convert.py song.mid --list

# 2. Convert
python maestro_convert.py song.mid -o song.json --title "My Song" --artist "Someone" --transcriber "You"

# Right hand only (melody), 1.2x faster, up a tone
python maestro_convert.py score.mxl --parts 0 --speed 1.2 --transpose 2 -o song.json
```

| Option | What it does |
|---|---|
| `--parts 0,1` | Which tracks/parts to include (indices from `--list`) |
| `--instrument` | Piano, Harp, Lute, Bass, Flute, Bell, BellMagnanimous (sets the note range) |
| `--transpose N` | Shift by N semitones before fitting to range |
| `--no-auto-shift` | Don't auto-pick a whole-octave shift when most notes are out of range |
| `--speed X` | Playback speed multiplier (2.0 = twice as fast) |
| `--max-chord N` | Max notes per chord (default 4; keeps top + bottom note first). 0 = unlimited |
| `--chord-window MS` | Onsets this close together become one chord (default 30) |
| `--rest-min MS` / `--no-rests` | Silence needed before an `R:` entry is inserted (default 150) |
| `--no-repeats` | MusicXML: don't expand repeat signs |

Notes outside the instrument range are folded by octaves. A warning prints if more than 10% needed folding.

## Sheet music image/PDF -> JSON

1. Run [Audiveris](https://github.com/Audiveris/audiveris) on the PDF/image to get a `.mxl`
   (`Audiveris -batch -export -output out/ score.pdf`).
2. Open the `.mxl` in MuseScore, fix any misread notes/rhythms, and export MusicXML or MIDI.
3. Run this script on the result.

Skipping step 2 is where wrong notes slip through, so listen to the result first.

## Assumptions to verify in your app

These are at the top of `maestro_convert.py`:

- Middle octave is C4-B4 (MIDI 60-71) with no marker; `-` is C3-B3, `+` is C5-B5.
- The top C (MIDI 84) is written `C^`. If your app expects `C+^`, change `HIGH_C`.
- `BellMagnanimous` is assumed to cover MIDI 60-84. Adjust `RANGES` if not.
- Note duration is the time until the next onset, so held notes don't need separate handling.
  Rests are only inserted when everything has gone silent.
