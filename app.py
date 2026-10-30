import os, sys, shutil, tempfile
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
import maestro_convert as mc

app = FastAPI()
OK_EXT = (".mid", ".midi", ".mxl", ".musicxml", ".xml")


def save_upload(f: UploadFile) -> str:
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in OK_EXT:
        raise HTTPException(400, "Upload a .mid, .mxl or .musicxml file.")
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    with open(path, "wb") as out:
        shutil.copyfileobj(f.file, out)
    return path


def load_events(path, ids, speed, drums=False):
    if path.endswith((".mid", ".midi")):
        return mc.midi_to_events(path, ids, speed, drums)
    if drums:
        raise ValueError("DrumSet conversion needs a .mid file")
    tmp = mc.musicxml_to_midi(path, ids)
    try:
        return mc.midi_to_events(tmp, None, speed)
    finally:
        os.remove(tmp)


@app.post("/api/inspect")
def inspect(file: UploadFile = File(...)):
    path = save_upload(file)
    try:
        tracks, bpm = [], None
        if path.endswith((".mid", ".midi")):
            _, tempos, trs = mc.read_midi(path)
            bpm = round(6e7 / (tempos[1][1] if len(tempos) > 1 else tempos[0][1]))
            for i, t in enumerate(trs):
                if t["notes"] or t["drums"]:
                    label = t["name"] or f"Track {i}"
                    tracks.append({"index": i, "name": label + ("" if t["notes"] else " (drums)"),
                                   "notes": len(t["notes"]) or len(t["drums"])})
        else:
            score = mc._m21().converter.parse(path)
            for i, part in enumerate(score.parts):
                n = sum(len(x.pitches) for x in part.flatten().notes)
                if n:
                    tracks.append({"index": i, "name": str(part.partName or f"Part {i}"),
                                   "notes": n})
        return {"tracks": tracks, "bpm": bpm}
    except (Exception, SystemExit) as e:
        raise HTTPException(400, f"Could not read this file: {e}")
    finally:
        os.remove(path)


@app.post("/api/convert")
def convert(file: UploadFile = File(...), title: str = Form("Untitled"),
            artist: str = Form("Unknown"), transcriber: str = Form(""),
            instrument: str = Form("Piano"), parts: Optional[str] = Form(None),
            transpose: int = Form(0), speed: float = Form(1.0),
            max_chord: int = Form(4), min_pitch: int = Form(0),
            quantize: float = Form(0), min_gap: float = Form(110)):
    path = save_upload(file)
    try:
        ids = {int(x) for x in parts.split(",")} if parts else None
        drums = instrument == "DrumSet"
        events = load_events(path, ids, speed, drums)
        if drums:
            entries, stats = mc.build_drum_entries(events, quantize=quantize, min_gap=min_gap)
        else:
            entries, stats = mc.build_entries(events, instrument, transpose, max_chord=max_chord,
                                              min_pitch=min_pitch, quantize=quantize, min_gap=min_gap)
        mc.validate(entries)
    except (Exception, SystemExit) as e:
        raise HTTPException(400, f"Conversion failed: {e}")
    finally:
        os.remove(path)
    return {"song": {"name": title, "artist": artist, "transcriber": transcriber,
                     "instrument": instrument, "notes": entries,
                     "skipOctaveReset": False},
            "stats": stats}


@app.post("/api/midi")
def simplified_midi(file: UploadFile = File(...), instrument: str = Form("Piano"),
                    parts: Optional[str] = Form(None), transpose: int = Form(0),
                    speed: float = Form(1.0), max_chord: int = Form(4),
                    min_pitch: int = Form(0), quantize: float = Form(0),
                    min_gap: float = Form(110)):
    path = save_upload(file)
    fd, out = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    try:
        ids = {int(x) for x in parts.split(",")} if parts else None
        if instrument == "DrumSet":
            raise ValueError("MIDI export is not available for DrumSet")
        groups, _ = mc.prepare_groups(load_events(path, ids, speed), instrument,
                                      transpose, max_chord=max_chord, min_pitch=min_pitch,
                                      quantize=quantize, min_gap=min_gap)
        if not groups:
            raise ValueError("No notes left after filtering")
        mc.write_midi(out, groups)
    except (Exception, SystemExit) as e:
        os.remove(out)
        raise HTTPException(400, f"Export failed: {e}")
    finally:
        os.remove(path)
    return FileResponse(out, media_type="audio/midi", filename="simplified.mid",
                        background=BackgroundTask(os.remove, out))


BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True), name="static")
