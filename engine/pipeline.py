"""Turn one chess.com review recording into a finished Daily_Brilliancy Short."""
import cv2, numpy as np, subprocess, os, re, json, pathlib, sys
import pytesseract

ENGINE = pathlib.Path(__file__).resolve().parent
ROOT = ENGINE.parent

def board_bbox(fr):
    b = fr.astype(np.int16)
    light = (np.abs(b[:, :, 0] - 210) < 28) & (np.abs(b[:, :, 1] - 236) < 28) & (np.abs(b[:, :, 2] - 236) < 28)
    dark = (np.abs(b[:, :, 0] - 86) < 30) & (np.abs(b[:, :, 1] - 150) < 30) & (np.abs(b[:, :, 2] - 118) < 30)
    m = (light | dark).astype(np.uint8)
    cols, rows = m.sum(0), m.sum(1)
    if cols.max() < 50: raise RuntimeError("не нашёл шахматную доску в записи")
    xs = np.where(cols > 0.35 * cols.max())[0]; ys = np.where(rows > 0.35 * rows.max())[0]
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)

def normalize(src, dst):
    """Bring any recording to the standard layout: 1920x1080 @ 60 fps, board at (400,76)-(1300,976),
    player bars above/below it. Handles other window sizes and Telegram-compressed videos."""
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 60
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(1, min(30, max(0, n - 1))); ok, fr = cap.read()
    if not ok: raise RuntimeError("не удалось прочитать видео")
    x0, y0, x1, y1 = board_bbox(fr)
    size = ((x1 - x0) + (y1 - y0)) / 2
    if (W, H) == (1920, 1080) and abs(fps - 60) < 0.5 and abs(x0 - 400) <= 3 and abs(y0 - 76) <= 3 and abs(size - 900) <= 4:
        return src, False
    s = 900.0 / size
    bg = fr[max(0, y0 // 2), min(W - 1, x1 - 6)].tolist()          # player-bar background
    sx0, sy0 = int(round(x0 * s)), int(round(y0 * s))
    # chess.com UI may be zoomed differently: scale the player bars so they match the standard bar height
    sb = s if y0 * s >= 70 else min(74.0 / max(y0, 1), 2.5 * s)
    cap = cv2.VideoCapture(src)
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1920x1080",
                          "-r", f"{fps:.5f}", "-i", "-", "-vf", "fps=60", "-c:v", "libx264", "-crf", "10", "-preset", "veryfast",
                          "-pix_fmt", "yuv420p", dst], stdin=subprocess.PIPE)
    while True:
        ok, fr = cap.read()
        if not ok: break
        S = cv2.resize(fr, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
        B = S if sb == s else cv2.resize(fr, None, fx=sb, fy=sb, interpolation=cv2.INTER_CUBIC)
        out = np.empty((1080, 1920, 3), np.uint8); out[:] = bg
        def put(img, cx0, rows_src, d0):
            a0, a1 = max(0, rows_src[0]), min(img.shape[0], rows_src[1])
            c0, c1 = max(0, cx0 - 8), min(img.shape[1], cx0 + 908)
            blk = img[a0:a1, c0:c1]
            dx = 392 + (c0 - (cx0 - 8))
            h = min(blk.shape[0], 1080 - d0); w = min(blk.shape[1], 1920 - dx)
            if h > 0 and w > 0: out[d0:d0 + h, dx:dx + w] = blk[:h, :w]
        put(S, sx0, (sy0, sy0 + 900), 76)                                   # board
        by0, by1, bx0 = int(round(y0 * sb)), int(round(y1 * sb)), int(round(x0 * sb))
        put(B, bx0, (max(0, by0 - 76), by0 - 2), 0)                         # top player bar (stop before the board edge line)
        put(B, bx0, (by1, by1 + 90), 976)                                    # bottom player bar
        p.stdin.write(out.tobytes())
    p.stdin.close(); p.wait()
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
    from collections import Counter
    votes = Counter()
    for x0 in (452, 460, 470):
        for sc in (3, 5):
            for mask in (False, True):
                c = fr[y0:y1, x0:1000].astype(np.int16)
                if mask:
                    g = c.min(2); g[(c.max(2) - c.min(2)) > 45] = 30   # drop coloured things (GM badge, flags)
                    g = g.astype(np.uint8)
                else:
                    g = cv2.cvtColor(fr[y0:y1, x0:1000], cv2.COLOR_BGR2GRAY)
                g = 255 - cv2.resize(g, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                g = cv2.copyMakeBorder(g, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
                t = clean_name(pytesseract.image_to_string(g, config="--psm 7").strip())
                if t: votes[t] += 1
    if not votes: return "Unknown"
    top = max(votes.values())
    return max((t for t, v in votes.items() if v == top), key=lambda t: sum(ch.isalpha() for ch in t))

def clean_name(t):
    t = t.split("(")[0]                                     # rating and everything after it
    t = re.sub(r"[^A-Za-zÀ-ÿ0-9_'\.\- ]", " ", t)
    words = [w for w in t.split() if w.strip(".-'_")]
    while len(words) > 1 and (len(words[0].strip(".")) <= 2 or words[0].upper() in ("GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "NM")):
        words.pop(0)                                         # avatar edge / title badge noise
    words = [("I" + w[1:]) if len(w) > 2 and w.startswith("ll") and w[2].islower() else w for w in words]
    if len(words) >= 2 and len(words[-1].strip(".")) == 1:   # "Surname Firstname I" -> "Firstname I Surname"
        words = words[1:] + words[:1]
    return " ".join(w.strip(".") if len(w.strip(".")) == 1 else w for w in words).strip()

def camel(display):
    if " " not in display.strip(): return display.strip()      # chess.com nickname: keep as is
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
