#!/usr/bin/env python3
"""
maestro_convert.py - convert MIDI or MusicXML into Maestro (GW2) song JSON.

  python maestro_convert.py song.mid --list                  # show tracks/parts
  python maestro_convert.py song.mid -o song.json --title "My Song"
  python maestro_convert.py score.mxl --parts 0 --title "My Song"   # right hand only

MIDI needs no extra packages. MusicXML (.mxl/.musicxml/.xml) needs: pip install music21
"""
import argparse, bisect, copy, json, os, re, struct, sys, tempfile

# ---------------------------------------------------------------------------
# Format assumptions - verify these against your Maestro app.
# Entry syntax:  Note[#][+/-][^]:DurationMs      (middle C = MIDI 60, no marker)
# ---------------------------------------------------------------------------
HIGH_C = "C^"                  # how the 8th key (top C) is written
MID_C = 60                     # C4 = middle octave, no +/- marker
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# (lowest, highest) MIDI pitch per instrument. C3..C6 = low, middle, high octave + top C.
RANGES = {
    "Piano": (48, 84), "Harp": (48, 84), "Lute": (48, 84), "Bass": (48, 84),
    "Flute": (48, 84), "Bell": (48, 84),
    "BellMagnanimous": (60, 84),   # 2-octave; ASSUMED to be middle + high
    "DrumSet": (48, 84),           # placeholder: drums use DRUM_MAP, not pitches
}
ENTRY_RE = re.compile(r"^(?:R|[A-G]#?[+-]?\^?|b|s|x|g|ht|mt|ft|cr|rd|hc|ho|hf):\d+$")
# General-MIDI drum note -> Maestro DrumSet code
DRUM_MAP = {35: "b", 36: "b", 38: "s", 40: "s", 37: "x", 41: "ft", 43: "ft", 45: "mt",
            47: "mt", 48: "ht", 50: "ht", 42: "hc", 44: "hf", 46: "ho", 49: "cr",
            52: "cr", 55: "cr", 57: "cr", 51: "rd", 53: "rd", 59: "rd"}


# ---------------------------------------------------------------------------
# Minimal Standard MIDI File reader (no dependencies)
# ---------------------------------------------------------------------------
def _vlq(data, pos):
    val = 0
    while True:
        b = data[pos]; pos += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, pos


def read_midi(path):
    """Returns (ticks_per_beat, tempo_changes[(tick, usec_per_beat)], tracks).
    Each track is {'name': str, 'notes': [(start_tick, end_tick, pitch)]}."""
    data = open(path, "rb").read()
    if data[:4] != b"MThd":
        raise ValueError("Not a MIDI file")
    _, ntrks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    pos = 8 + struct.unpack(">I", data[4:8])[0]
    tempos, tracks = [(0, 500000)], []
    for _ in range(ntrks):
        if data[pos:pos + 4] != b"MTrk":
            break
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        p, end, tick, status = pos + 8, pos + 8 + length, 0, 0
        name, notes, drums, open_notes = "", [], [], {}
        while p < end:
            delta, p = _vlq(data, p)
            tick += delta
            b = data[p]
            if b == 0xFF:                                   # meta
                mtype = data[p + 1]
                ln, q = _vlq(data, p + 2)
                body = data[q:q + ln]
                if mtype == 0x03 and not name:
                    name = body.decode("latin-1", "replace")
                elif mtype == 0x51 and ln == 3:
                    tempos.append((tick, int.from_bytes(body, "big")))
                p = q + ln
                continue
            if b in (0xF0, 0xF7):                           # sysex
                ln, q = _vlq(data, p + 1)
                p = q + ln
                continue
            if b & 0x80:
                status = b; p += 1
            kind, ch = status >> 4, status & 0x0F
            if kind in (0xC, 0xD):
                p += 1
                continue
            d1, d2 = data[p], data[p + 1]
            p += 2
            if kind == 0x9 and d2 > 0:
                open_notes.setdefault((ch, d1), []).append(tick)
            elif kind == 0x8 or (kind == 0x9 and d2 == 0):
                starts = open_notes.get((ch, d1))
                if starts:
                    (drums if ch == 9 else notes).append((starts.pop(0), tick, d1))
        for (ch, pitch), starts in open_notes.items():       # unterminated notes
            for s in starts:
                (drums if ch == 9 else notes).append((s, tick, pitch))
        tracks.append({"name": name, "notes": sorted(notes), "drums": sorted(drums)})
        pos = end
    return division, tempos, tracks


def midi_to_events(path, track_ids=None, speed=1.0, drums=False):
    """-> list of (start_ms, end_ms, pitch), tempo changes applied."""
    tpb, tempos, tracks = read_midi(path)
    tempos = sorted(tempos, key=lambda t: t[0])              # stable: later wins at same tick
    ticks, segs, sec, prev_t, prev_tempo = [], [], 0.0, 0, tempos[0][1]
    for t, tempo in tempos:
        sec += (t - prev_t) * prev_tempo / tpb / 1e6
        ticks.append(t); segs.append((t, sec, tempo))
        prev_t, prev_tempo = t, tempo

    def to_ms(tick):
        t0, s0, tempo = segs[bisect.bisect_right(ticks, tick) - 1]
        return (s0 + (tick - t0) * tempo / tpb / 1e6) * 1000.0 / speed

    events = []
    for i, tr in enumerate(tracks):
        if track_ids is not None and i not in track_ids:
            continue
        events += [(to_ms(s), to_ms(e), p) for s, e, p in tr["drums" if drums else "notes"]]
    return events


def list_midi(path):
    _, _, tracks = read_midi(path)
    for i, tr in enumerate(tracks):
        ps = [p for _, _, p in tr["notes"]]
        if ps:
            print(f"  [{i}] {tr['name'] or '(unnamed)':24} {len(ps):5} notes   "
                  f"pitch {min(ps)}-{max(ps)}")


# ---------------------------------------------------------------------------
# MusicXML via music21 (converted to a temp MIDI so tempo/ties are handled by music21)
# ---------------------------------------------------------------------------
def _m21():
    try:
        import music21
        return music21
    except ImportError:
        sys.exit("MusicXML input needs music21:  pip install music21")


def list_musicxml(path):
    m21 = _m21()
    score = m21.converter.parse(path)
    for i, part in enumerate(score.parts):
        ps = [p.midi for n in part.flatten().notes for p in n.pitches]
        if ps:
            print(f"  [{i}] {str(part.partName or '(unnamed)'):24} {len(ps):5} notes   "
                  f"pitch {min(ps)}-{max(ps)}")


def musicxml_to_midi(path, part_ids, expand_repeats=True):
    m21 = _m21()
    score = m21.converter.parse(path)
    if expand_repeats:
        try:
            score = score.expandRepeats()
        except Exception as e:
            print(f"warning: could not expand repeats ({e}); playing straight through",
                  file=sys.stderr)
    parts = list(score.parts)
    ids = part_ids if part_ids is not None else range(len(parts))
    new = m21.stream.Score()
    for i in ids:
        new.insert(0, copy.deepcopy(parts[i]))
    marks = list(score.flatten().getElementsByClass(m21.tempo.MetronomeMark))
    if marks and not list(new.flatten().getElementsByClass(m21.tempo.MetronomeMark)):
        new.parts[0].insert(0, copy.deepcopy(marks[0]))
    fd, tmp = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    new.write("midi", fp=tmp)
    return tmp


# ---------------------------------------------------------------------------
# Events -> Maestro entries
# ---------------------------------------------------------------------------
def best_octave_shift(pitches, lo, hi):
    """Whole-octave shift (semitones). Folding one outlier costs one octave of
    displacement; shifting costs half an octave for *every* note, so a shift only
    wins when roughly half the notes would otherwise fall outside the range."""
    if not pitches:
        return 0

    def cost(k):
        folds = 0
        for p in pitches:
            q = p + 12 * k
            folds += -(-(lo - q) // 12) if q < lo else -(-(q - hi) // 12) if q > hi else 0
        return 0.5 * len(pitches) * abs(k) + folds

    return 12 * min(range(-4, 5), key=lambda k: (cost(k), abs(k)))


def fold(p, lo, hi):
    while p < lo: p += 12
    while p > hi: p -= 12
    return p


def fmt_note(p, ms, lo, hi):
    if p == hi and p % 12 == 0:                 # top C of the range
        return f"{HIGH_C}:{ms}"
    name = NAMES[p % 12]
    marker = "-" if p < MID_C else "+" if p >= MID_C + 12 else ""
    return f"{name}{marker}:{ms}"


def thin_chord(pitches, max_chord):
    """Keep highest (melody) and lowest (bass) first, then fill from the top."""
    ps = sorted(set(pitches))
    if max_chord <= 0 or len(ps) <= max_chord:
        return ps
    keep = {ps[-1], ps[0]} if max_chord > 1 else {ps[-1]}
    for p in reversed(ps):
        if len(keep) >= max_chord:
            break
        keep.add(p)
    return sorted(keep)


def thin_group(g, max_chord):
    by_pitch = {}
    for s, e, p in g:
        if p not in by_pitch or e > by_pitch[p][1]:
            by_pitch[p] = (s, e, p)
    return [by_pitch[p] for p in thin_chord(list(by_pitch), max_chord)]


def prepare_groups(events, instrument="Piano", transpose=0, auto_shift=True,
                   chord_window=30, max_chord=4, min_pitch=0, quantize=0, min_gap=0):
    """Filter, transpose, fit to range and group into chords.
    Returns (groups, stats); each group is a list of (start_ms, end_ms, pitch)."""
    lo, hi = RANGES[instrument]
    events = [(s, e, p + transpose) for s, e, p in events if p >= min_pitch]
    stats = {"notes": len(events), "shift": 0, "folded": 0}
    if not events:
        return [], stats
    if auto_shift:
        stats["shift"] = best_octave_shift([p for _, _, p in events], lo, hi)
        events = [(s, e, p + stats["shift"]) for s, e, p in events]

    folded = []
    for s, e, p in events:
        q = fold(p, lo, hi)
        stats["folded"] += q != p
        folded.append((s, e, q))
    if quantize:                                      # snap starts/ends to a grid
        folded = [(round(s / quantize) * quantize,
                   max(round(e / quantize) * quantize, round(s / quantize) * quantize + quantize), p)
                  for s, e, p in folded]
    folded.sort()

    # group onsets that start within chord_window of each other
    groups, cur = [], [folded[0]]
    for ev in folded[1:]:
        if ev[0] - cur[0][0] <= chord_window:
            cur.append(ev)
        else:
            groups.append(cur); cur = [ev]
    groups.append(cur)
    groups = [thin_group(g, max_chord) for g in groups]
    if min_gap:                                       # drop onsets too close to the last kept one
        kept = []
        for g in groups:
            if kept and g[0][0] - kept[-1][0][0] < min_gap:
                continue
            kept.append(g)
        stats["thinned"] = len(groups) - len(kept)
        groups = kept
    return groups, stats


def _enc_vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def write_midi(path, groups, tpb=480, tempo=500000):
    """Write the simplified groups as a single-track Standard MIDI File."""
    k = tpb * 1e6 / tempo / 1000.0                    # ticks per millisecond
    ev = []
    for g in groups:
        on = round(g[0][0] * k)
        for _, e, p in g:
            ev.append((on, 1, p))
            ev.append((max(round(e * k), on + round(50 * k)), 0, p))
    ev.sort()                                         # note-offs sort before note-ons
    trk = bytearray(b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big") + b"\x00\xc0\x00")
    last = 0
    for tick, kind, p in ev:
        trk += _enc_vlq(tick - last)
        trk += bytes([0x90 if kind else 0x80, p, 90 if kind else 0])
        last = tick
    trk += b"\x00\xff\x2f\x00"
    with open(path, "wb") as f:
        f.write(b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
                + (1).to_bytes(2, "big") + tpb.to_bytes(2, "big"))
        f.write(b"MTrk" + len(trk).to_bytes(4, "big") + bytes(trk))


def build_entries(events, instrument="Piano", transpose=0, auto_shift=True,
                  chord_window=30, max_chord=4, rests=True, rest_min=150,
                  min_ms=60, tail_ms=500, min_pitch=0, quantize=0, min_gap=0):
    lo, hi = RANGES[instrument]
    groups, stats = prepare_groups(events, instrument, transpose, auto_shift,
                                   chord_window, max_chord, min_pitch, quantize, min_gap)
    if not groups:
        return [], stats

    entries, t0 = [], round(groups[0][0][0])
    if rests and t0 >= rest_min:
        entries.append(f"R:{t0}")
    max_end = 0.0
    for gi, g in enumerate(groups):
        on = g[0][0]
        max_end = max(max_end, max(e for _, e, _ in g))
        pitches = [p for _, _, p in g]
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0][0]
            gap = nxt - on
            if rests and max_end < nxt - rest_min:
                dur = max(max_end - on, min_ms)
                rest = gap - dur
            else:
                dur, rest = gap, 0
        else:
            dur, rest = max(max_end - on, tail_ms), 0
        dur = max(round(dur), 1)
        entries.append(" ".join(fmt_note(p, dur, lo, hi) for p in pitches))
        if rest >= 1:
            entries.append(f"R:{round(rest)}")
    return entries, stats


def build_drum_entries(events, chord_window=30, quantize=0, min_gap=110, tail_ms=250):
    """events: (start_ms, end_ms, midi_drum_note) -> Maestro DrumSet entries."""
    hits = [((round(s / quantize) * quantize if quantize else s), DRUM_MAP[p])
            for s, _, p in events if p in DRUM_MAP]
    stats = {"notes": len(hits), "shift": 0, "folded": 0}
    if not hits:
        return [], stats
    hits.sort()
    groups, cur = [], [hits[0]]
    for h in hits[1:]:
        if h[0] - cur[0][0] <= chord_window:
            cur.append(h)
        else:
            groups.append(cur); cur = [h]
    groups.append(cur)
    kept = []
    for g in groups:
        if kept and g[0][0] - kept[-1][0][0] < min_gap:
            continue
        kept.append(g)
    entries, t0 = [], round(kept[0][0][0])
    if t0 >= 150:
        entries.append(f"R:{t0}")
    for i, g in enumerate(kept):
        dur = round(kept[i + 1][0][0] - g[0][0]) if i + 1 < len(kept) else tail_ms
        entries.append(" ".join(f"{c}:{max(dur, 1)}" for c in sorted({c for _, c in g})))
    return entries, stats


def validate(entries):
    for e in entries:
        for tok in e.split(" "):
            if not ENTRY_RE.match(tok):
                raise ValueError(f"Bad Maestro token: {tok!r}")


# ---------------------------------------------------------------------------
def parse_ids(s):
    return None if s is None else {int(x) for x in s.split(",")}


def main():
    ap = argparse.ArgumentParser(description="Convert MIDI/MusicXML to Maestro JSON")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="output .json (default: stdout)")
    ap.add_argument("--list", action="store_true", help="list tracks/parts and exit")
    ap.add_argument("--title"); ap.add_argument("--artist", default="Unknown")
    ap.add_argument("--transcriber", default="")
    ap.add_argument("--instrument", default="Piano", choices=list(RANGES))
    ap.add_argument("--parts", help="comma-separated track/part indices (see --list)")
    ap.add_argument("--transpose", type=int, default=0, help="semitones")
    ap.add_argument("--no-auto-shift", action="store_true",
                    help="don't pick the best whole-octave shift automatically")
    ap.add_argument("--speed", type=float, default=1.0, help="2.0 = twice as fast")
    ap.add_argument("--max-chord", type=int, default=4, help="max notes per chord (0 = no limit)")
    ap.add_argument("--chord-window", type=float, default=30, help="ms; onsets this close = one chord")
    ap.add_argument("--min-pitch", type=int, default=0,
                    help="drop notes below this MIDI pitch (60 = middle C; roughly keeps the right hand)")
    ap.add_argument("--style", choices=["full", "melody", "melody-bass"], default="full",
                    help="melody = top note only; melody-bass = top + bottom note; full = --max-chord")
    ap.add_argument("--quantize", type=float, default=0,
                    help="snap timing to a grid in ms (60000 / BPM / 4 = sixteenth notes; 0 = off)")
    ap.add_argument("--min-gap", type=float, default=110,
                    help="ms; drop notes that start closer than this to the previous one (0 = off)")
    ap.add_argument("--midi-out", help="also write the simplified result as a .mid file")
    ap.add_argument("--no-rests", action="store_true", help="never insert R: entries inside the song")
    ap.add_argument("--rest-min", type=float, default=150, help="ms of silence before a rest is inserted")
    ap.add_argument("--no-repeats", action="store_true", help="MusicXML: don't expand repeats")
    ap.add_argument("--skip-octave-reset", action="store_true")
    a = ap.parse_args()

    ext = os.path.splitext(a.input)[1].lower()
    is_midi = ext in (".mid", ".midi")
    if not is_midi and ext not in (".mxl", ".musicxml", ".xml"):
        sys.exit("Input must be .mid/.midi or .mxl/.musicxml/.xml")

    if a.list:
        (list_midi if is_midi else list_musicxml)(a.input)
        return

    a.max_chord = {"melody": 1, "melody-bass": 2}.get(a.style, a.max_chord)
    drums = a.instrument == "DrumSet"
    if drums and not is_midi:
        sys.exit("DrumSet conversion needs a .mid file")
    ids, tmp = parse_ids(a.parts), None
    if is_midi:
        events = midi_to_events(a.input, ids, a.speed, drums)
    else:
        tmp = musicxml_to_midi(a.input, ids, not a.no_repeats)
        try:
            events = midi_to_events(tmp, None, a.speed)
        finally:
            os.remove(tmp)

    if drums:
        entries, st = build_drum_entries(events, a.chord_window, a.quantize, a.min_gap)
    else:
        entries, st = build_entries(
            events, a.instrument, a.transpose, not a.no_auto_shift, a.chord_window,
            a.max_chord, not a.no_rests, a.rest_min, min_pitch=a.min_pitch,
            quantize=a.quantize, min_gap=a.min_gap)
    validate(entries)
    if a.midi_out and not drums:
        groups, _ = prepare_groups(events, a.instrument, a.transpose, not a.no_auto_shift,
                                   a.chord_window, a.max_chord, a.min_pitch,
                                   a.quantize, a.min_gap)
        write_midi(a.midi_out, groups)
        print(f"wrote {a.midi_out}", file=sys.stderr)

    song = {"name": a.title or os.path.splitext(os.path.basename(a.input))[0],
            "artist": a.artist, "transcriber": a.transcriber,
            "instrument": a.instrument, "notes": entries,
            "skipOctaveReset": a.skip_octave_reset}
    out = json.dumps(song, indent=2)
    if a.output:
        open(a.output, "w").write(out)
    elif not a.midi_out:
        print(out)
    print(f"{st['notes']} notes -> {len(entries)} entries | octave shift {st['shift']:+d} semitones | "
          f"{st['folded']} notes folded into range", file=sys.stderr)
    if st["folded"] > st["notes"] * 0.1:
        print("warning: many notes were folded; consider --parts to pick one hand, "
              "--transpose, or a wider instrument", file=sys.stderr)


if __name__ == "__main__":
    main()
