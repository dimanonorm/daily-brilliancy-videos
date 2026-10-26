"""Turn one chess.com review recording into a finished Daily_Brilliancy Short."""
import cv2, numpy as np, subprocess, os, re, json, pathlib, sys
import pytesseract

ENGINE = pathlib.Path(__file__).resolve().parent
ROOT = ENGINE.parent

def normalize(src, dst):
    """Make sure the recording is 1920x1080 @ 60 fps (Telegram may compress 'video' uploads)."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate", "-of", "csv=p=0", src], capture_output=True, text=True).stdout.strip()
    w, h, r = p.split(",")[:3]
    num, den = (r.split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    if (w, h) == ("1920", "1080") and abs(fps - 60) < 0.5:
        return src, False
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", src, "-vf", "scale=1920:1080:flags=lanczos,fps=60",
                    "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-an", dst], check=True)
    return dst, True

def scan(path):
    cap = cv2.VideoCapture(path); prev = None; d, top, teal = [], [], []; fr120 = None; i = 0
    while True:
        ok, fr = cap.read()
        if not ok: break
        if i == 120: fr120 = fr.copy()
        g = cv2.cvtColor(fr[76:976, 400:1300], cv2.COLOR_BGR2GRAY).astype(np.int32)
        if prev is None: d.append(0); top.append(0)
        else:
            a = np.abs(g - prev); d.append(int(a.sum())); top.append(int(a[:896, :896].reshape(8, 112, 8, 112).sum((1, 3)).max()))
        prev = g
        b = fr[60:980, 390:1306].astype(np.int16)
        teal.append(int(((np.abs(b[:, :, 2] - 27) < 30) & (np.abs(b[:, :, 1] - 172) < 30) & (np.abs(b[:, :, 0] - 166) < 30)).sum()))
        i += 1
    if fr120 is None:
        cap = cv2.VideoCapture(path); ok, fr120 = cap.read()
    return np.array(d), np.array(top), np.array(teal), fr120

def find_move(d, top, teal):
    pops = [k for k in range(1, len(teal)) if teal[k] > 3000 and teal[k - 1] <= 3000]
    if not pops:
        raise RuntimeError("не нашёл в записи значок «!!» (блестящий ход)")
    s = pops[-1]
    onset = None
    for j in range(s, max(6, s - 150), -1):
        if top[j] > 20000 and all(top[j - q] < 20000 for q in range(1, 7)) and top[j:j + 8].sum() > 300000:
            onset = j; break
    if onset is None:
        raise RuntimeError("не смог точно определить момент блестящего хода")
    st = onset + 1
    while st < len(top) - 10 and not all(top[st:st + 10] < 5000): st += 1
    return onset, st, pops

def orientation(fr):
    lab = cv2.cvtColor(fr[78:112, 400:428], cv2.COLOR_BGR2GRAY).astype(np.float32)
    s = [float(cv2.matchTemplate(lab, cv2.imread(str(ENGINE / f), 0).astype(np.float32), cv2.TM_CCOEFF_NORMED).max())
         for f in ("rank_top_normal.png", "rank_top_flipped.png")]
    return "normal" if s[0] >= s[1] else "flipped"

def ocr_name(fr, y0, y1):
    g = cv2.cvtColor(fr[y0:y1, 460:1000], cv2.COLOR_BGR2GRAY)
    g = 255 - cv2.resize(g, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    g = cv2.copyMakeBorder(g, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    t = pytesseract.image_to_string(g, config="--psm 7").strip()
    t = re.sub(r"\(.*?\)", " ", t)                       # ratings
    t = re.sub(r"^(GM|IM|FM|CM|WGM|WIM|WFM|NM)\s+", "", t)  # titles
    t = re.sub(r"[^A-Za-zÀ-ÿ'\.\- ]", " ", t)
    words = [w for w in t.split() if len(w.strip(".-'")) > 0]
    while words and len(words[0]) == 1 and not words[0].isupper(): words.pop(0)
    # chess.com sometimes shows "Surname Firstname I" (initial last) -> "Firstname I Surname"
    if len(words) >= 2 and len(words[-1].strip(".")) == 1:
        words = words[1:] + words[:1]
    return " ".join(w.strip(".") if len(w.strip(".")) == 1 else w for w in words).strip() or "Unknown"

def camel(display):
    parts = re.split(r"[\s\-\.]+", display.replace("'", ""))
    return "".join(p[:1].upper() + p[1:] for p in parts if p)

def render(rec, F0, F1, out, song, aoff):
    env = {**os.environ, "REC": rec}
    subprocess.run([sys.executable, str(ENGINE / "render.py"), str(F0), str(F1), out, song, f"{aoff:.4f}"],
                   env=env, check=True, stdout=subprocess.DEVNULL)

def verify(out, move):
    cap = cv2.VideoCapture(out); s = int(round(move * 60)) - 20; cap.set(1, max(0, s)); prev = None
    for i in range(max(0, s), s + 30):
        ok, fr = cap.read()
        if not ok: break
        g = cv2.cvtColor(fr[804:2894:4, 36:2126:4], cv2.COLOR_BGR2GRAY).astype(int)
        if prev is not None and np.abs(g - prev).sum() > 150000: return i / 60
        prev = g
    return None

def process(rec, song_path, move_at, workdir):
    rec2, norm = normalize(rec, os.path.join(workdir, "norm.mp4"))
    d, top, teal, fr = scan(rec2)
    onset, settle, pops = find_move(d, top, teal)
    ori = orientation(fr)
    top_name, bot_name = ocr_name(fr, 0, 34), ocr_name(fr, 983, 1017)
    white, black = (bot_name, top_name) if ori == "normal" else (top_name, bot_name)
    frames = len(d)
    F0 = max(0, onset - round(move_at * 60)); F1 = min(settle + 180, frames)
    aoff = move_at - onset / 60 if onset < round(move_at * 60) else 0.0
    out = os.path.join(workdir, "out.mp4")
    render(rec2, F0, F1, out, song_path, aoff)
    move = (onset - F0) / 60
    got = verify(out, move)
    return {"white": white, "black": black, "out": out, "move_s": move, "verified_s": got,
            "duration": (F1 - F0) / 60, "normalized": norm, "onset": onset, "settle": settle}
