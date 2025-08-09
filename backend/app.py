import os, io, json, time, uuid, re, unicodedata
from typing import List
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Your existing splitter (keep parser_utils.py next to this file)
from parser_utils import split_and_merge

# PDF extractors
import pdfplumber
from pdfminer.high_level import extract_text as pm_extract_text

app = Flask(__name__)
CORS(app)

BASE_DIR   = os.path.dirname(__file__)
PARSED_DIR = os.path.join(BASE_DIR, "parsed")
SNAP_DIR   = os.path.join(PARSED_DIR, "snapshots")
os.makedirs(PARSED_DIR, exist_ok=True)
os.makedirs(SNAP_DIR,   exist_ok=True)

# ------------------------------
# Text cleanup (kills \u0000 etc.)
# ------------------------------
STOP_HEADINGS = {
    "find me online","contact","links","social",
    "key achievements","education","certifications","languages","hobbies",
    "interests","awards","publications","courses","strengths",
    "objective","profile","summary","skills","tools","technologies",
}
STOP_SUBSTRINGS = ("linkedin.com","github.com","www.","http://","https://","@gmail.com","email","phone")

def clean_line(s: str) -> str:
    if not s: return ""
    s = s.replace("\u0000", "")
    s = re.sub(r"[\ue000-\uf8ff]", "", s)  # private-use glyphs
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def is_noise(s: str) -> bool:
    low = s.lower()
    if low in STOP_HEADINGS: return True
    if any(tok in low for tok in STOP_SUBSTRINGS): return True
    if len(low) <= 2: return True
    return False

def preprocess_lines(lines):
    out = []
    for ln in lines or []:
        ln = clean_line(str(ln))
        if not ln: continue
        if is_noise(ln): continue
        out.append(ln)
    return out

# ------------------------------
# PDF text extraction
# ------------------------------
def extract_text_multistrategy(file_storage) -> str:
    data = file_storage.read()
    try: file_storage.seek(0)
    except Exception: pass

    text = ""
    # 1) pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            parts = [(p.extract_text(x_tolerance=2, y_tolerance=2) or "") for p in pdf.pages]
            text = "\n".join(parts).strip()
    except Exception:
        pass

    # 2) pdfminer.six
    if not text:
        try:
            text = (pm_extract_text(io.BytesIO(data)) or "").strip()
        except Exception:
            pass

    # 3) pypdfium2
    if not text:
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(io.BytesIO(data))
            parts=[]
            for i in range(len(pdf)):
                tp = pdf[i].get_textpage()
                parts.append(tp.get_text_range())
                tp.close()
            pdf.close()
            text = "\n".join(parts).strip()
        except Exception:
            pass

    return text

def extract_lines_from_pdf(file_storage) -> List[str]:
    t = extract_text_multistrategy(file_storage)
    return [ln for ln in (t.splitlines() if t else []) if ln is not None]

# ------------------------------
# File helpers
# ------------------------------
def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def _now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%S")

def _current_id_path():
    return os.path.join(SNAP_DIR, "current.txt")

def _get_current_id():
    p = _current_id_path()
    return _read_text(p).strip() if os.path.exists(p) else ""

def _set_current_id(resume_id):
    with open(_current_id_path(), "w", encoding="utf-8") as f:
        f.write(resume_id)

# ------------------------------
# Snapshot storage
# ------------------------------
def save_snapshot(experience, projects, uploaded_pdf, original_name) -> dict:
    resume_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = os.path.join(SNAP_DIR, resume_id)
    os.makedirs(d, exist_ok=True)

    # parsed data
    data = {"experience": experience, "projects": projects}
    _write_json(os.path.join(d, "data.json"), data)

    # original pdf
    try:
        uploaded_pdf.seek(0)
    except Exception:
        pass
    uploaded_pdf.save(os.path.join(d, "resume.pdf"))

    # metadata
    meta = {
        "id": resume_id,
        "uploaded_at": _now_iso(),
        "filename": original_name,
        "counts": {"experience": len(experience), "projects": len(projects)},
    }
    _write_json(os.path.join(d, "meta.json"), meta)

    # update index (newest first)
    index_path = os.path.join(SNAP_DIR, "index.json")
    idx = _read_json(index_path, {"items": []})
    idx["items"].insert(0, meta)
    _write_json(index_path, idx)

    # set current + legacy write for backward compat
    _set_current_id(resume_id)
    _write_json(os.path.join(PARSED_DIR, "data.json"), data)

    return meta

def list_snapshots():
    idx = _read_json(os.path.join(SNAP_DIR, "index.json"), {"items": []})
    return idx.get("items", [])

# ------------------------------
# Profile merge
# ------------------------------
def load_profile_data(resume_id: str | None = None) -> dict:
    """
    Merge global profile.json (name, links, summary, skills)
    with resume-specific data.json from snapshot (or current/legacy).
    """
    payload = {
        "name": "", "links": {}, "summary": "", "skills": [],
        "experience": [], "projects": [],
        "resume_id": "", "available_resumes": []
    }

    # global profile
    profile = _read_json(os.path.join(PARSED_DIR, "profile.json"), {})
    for k in ("name", "links", "summary", "skills"):
        if k in profile:
            payload[k] = profile[k]

    # choose snapshot
    chosen = resume_id or _get_current_id()
    data_path = None
    if chosen:
        cand = os.path.join(SNAP_DIR, chosen, "data.json")
        if os.path.exists(cand):
            data_path = cand
            payload["resume_id"] = chosen

    # fallback to legacy
    if not data_path:
        legacy = os.path.join(PARSED_DIR, "data.json")
        if os.path.exists(legacy):
            data_path = legacy
            payload["resume_id"] = "legacy"

    if data_path:
        parsed = _read_json(data_path, {})
        payload["experience"] = parsed.get("experience", [])
        payload["projects"]   = parsed.get("projects", [])

    payload["available_resumes"] = list_snapshots()
    return payload

# ------------------------------
# Routes
# ------------------------------
@app.get("/ping")
def ping():
    return {"status": "ok"}

@app.post("/parse-resume")
def parse_resume():
    uploaded = request.files.get("resume") or request.files.get("file")
    if not uploaded:
        return jsonify({"error": "Upload a PDF in form field 'resume' or 'file'"}), 400

    raw_lines = extract_lines_from_pdf(uploaded)
    lines = preprocess_lines(raw_lines)
    exp, proj = split_and_merge(lines)
    meta = save_snapshot(exp, proj, uploaded, uploaded.filename)

    return jsonify({
        "message": "snapshot saved",
        "resume_id": meta["id"],
        "meta": meta,
        "preview": {"experience": exp[:5], "projects": proj[:5]}
    })

@app.get("/api/resumes")
def api_resumes():
    return jsonify({
        "current": _get_current_id(),
        "items": list_snapshots()
    })

@app.post("/api/use-resume")
def api_use_resume():
    rid = request.args.get("resume_id") or (request.json or {}).get("resume_id")
    if not rid or not os.path.exists(os.path.join(SNAP_DIR, rid, "data.json")):
        return jsonify({"error": "resume_id not found"}), 404
    _set_current_id(rid)
    return jsonify({"ok": True, "current": rid})

@app.get("/api/profile-data")
def api_profile_data():
    rid = request.args.get("resume_id")  # optional
    return jsonify(load_profile_data(rid))

@app.get("/parsed/<path:filename>")
def get_parsed(filename):
    return send_from_directory(PARSED_DIR, filename, mimetype="application/json")

@app.get("/snapshots/<rid>/<path:filename>")
def get_snapshot_file(rid, filename):
    # e.g. /snapshots/<id>/resume.pdf or data.json
    return send_from_directory(os.path.join(SNAP_DIR, rid), filename)

@app.get("/")
def root():
    return jsonify({
        "ok": True,
        "endpoints": {
            "upload": "POST /parse-resume (form: resume=file.pdf)",
            "list_resumes": "GET /api/resumes",
            "use_resume": "POST /api/use-resume?resume_id=<id>",
            "profile_data": "GET /api/profile-data[?resume_id=<id>]",
            "download_snapshot": "GET /snapshots/<id>/resume.pdf"
        }
    })

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)

