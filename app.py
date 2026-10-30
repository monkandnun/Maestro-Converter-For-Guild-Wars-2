import os, sys, shutil, tempfile
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
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


@app.post("/api/inspect")
def inspect(file: UploadFile = File(...)):
    path = save_upload(file)
    try:
        tracks = []
        if path.endswith((".mid", ".midi")):
            _, _, trs = mc.read_midi(path)
            for i, t in enumerate(trs):
                if t["notes"]:
                    tracks.append({"index": i, "name": t["name"] or f"Track {i}",
                                   "notes": len(t["notes"])})
        else:
            score = mc._m21().converter.parse(path)
            for i, part in enumerate(score.parts):
                n = sum(len(x.pitches) for x in part.flatten().notes)
                if n:
                    tracks.append({"index": i, "name": str(part.partName or f"Part {i}"),
                                   "notes": n})
        return {"tracks": tracks}
    except (Exception, SystemExit) as e:
        raise HTTPException(400, f"Could not read this file: {e}")
    finally:
        os.remove(path)


@app.post("/api/convert")
def convert(file: UploadFile = File(...), title: str = Form("Untitled"),
            artist: str = Form("Unknown"), transcriber: str = Form(""),
            instrument: str = Form("Piano"), parts: Optional[str] = Form(None),
            transpose: int = Form(0), speed: float = Form(1.0),
            max_chord: int = Form(4)):
    path = save_upload(file)
    try:
        ids = {int(x) for x in parts.split(",")} if parts else None
        if path.endswith((".mid", ".midi")):
            events = mc.midi_to_events(path, ids, speed)
        else:
            tmp = mc.musicxml_to_midi(path, ids)
            try:
                events = mc.midi_to_events(tmp, None, speed)
            finally:
                os.remove(tmp)
        entries, stats = mc.build_entries(events, instrument, transpose,
                                          max_chord=max_chord)
        mc.validate(entries)
    except (Exception, SystemExit) as e:
        raise HTTPException(400, f"Conversion failed: {e}")
    finally:
        os.remove(path)
    return {"song": {"name": title, "artist": artist, "transcriber": transcriber,
                     "instrument": instrument, "notes": entries,
                     "skipOctaveReset": False},
            "stats": stats}


BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True), name="static")