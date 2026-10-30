"""Format 2 Short: vertical 2160x3840, board + player bars, grandmaster photo with name fades in,
on the brilliant move the board switches to a dark grade and soft snow falls. Fixed music track.

usage:
  python3 format2.py REC.mkv PHOTO.jpg "GARRY KASPAROV" OUT.mp4 [--land FRAME] [--track f2track.m4a]
           [--preview 600,830,900]
REC   chess.com review recording (any size; board auto-detected)
PHOTO portrait of the player shown at the BOTTOM of the recording (background is removed automatically)
"""
import argparse, os, pathlib, subprocess, sys, urllib.request
import numpy as np, cv2

HERE = pathlib.Path(__file__).resolve().parent
MODELS = pathlib.Path(os.environ.get('F2_MODELS', str(pathlib.Path.home() / 'f2_models')))
W, H, FPS = 2160, 3840, 60
# ---- template timing (frames of the music track, 60 fps) ----
NOUT = 1087            # 18.117 s
LAND = 813             # the brilliant piece arrives on its square
GRADE_AT = 817         # dark grade + snow start (beat)
FIN0, FIN1, FOUT0, FOUT1 = 584, 620, 698, 734   # photo fade in / out
# ---- layout (same as the regular Daily_Brilliancy shorts) ----
BG = (25, 29, 31); PANEL = (20, 22, 24)
TP = (37, 520, 2126, 781); BP = (37, 2925, 2126, 3182)
BX, BY, BS = 36, 804, 2090


def board_bbox(fr):
    b = fr.astype(np.int16)
    light = (np.abs(b[..., 0] - 210) < 28) & (np.abs(b[..., 1] - 236) < 28) & (np.abs(b[..., 2] - 236) < 28)
    dark = (np.abs(b[..., 0] - 86) < 30) & (np.abs(b[..., 1] - 150) < 30) & (np.abs(b[..., 2] - 118) < 30)
    m = (light | dark).astype(np.uint8)
    cols, rows = m.sum(0), m.sum(1)
    xs = np.where(cols > 0.35 * cols.max())[0]; ys = np.where(rows > 0.35 * rows.max())[0]
    return int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)


def find_land(rec, bb):
    """Frame where the last brilliant (teal !!) move's piece arrives."""
    x0, y0, x1, y1 = bb
    cap = cv2.VideoCapture(rec); prev = None; d = []; teal = []
    while True:
        ok, f = cap.read()
        if not ok: break
        a = cv2.resize(f[y0:y1, x0:x1], (220, 220), interpolation=cv2.INTER_AREA).astype(np.int16)
        d.append(0 if prev is None else int(np.abs(a - prev).sum())); prev = a
        t = (np.abs(a[..., 2] - 38) < 25) & (np.abs(a[..., 1] - 160) < 30) & (np.abs(a[..., 0] - 150) < 30)
        teal.append(int(t.sum()))
    d = np.array(d); teal = np.array(teal)
    # last appearance of the !! popup
    on = [i for i in range(1, len(teal)) if teal[i] > 25 and teal[i - 1] <= 25]
    if not on: raise RuntimeError('brilliant popup not found; pass --land')
    p = on[-1]
    plateau = teal[p:p + 40].max()
    land = next(i for i in range(p, p + 40) if teal[i] >= 0.6 * plateau)
    return land, len(d)


def build_overlay(photo, name, size):
    import onnxruntime as ort
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    MODELS.mkdir(parents=True, exist_ok=True)
    for m in ('isnet-general-use', 'u2net_human_seg'):
        p = MODELS / f'{m}.onnx'
        if not p.exists():
            urllib.request.urlretrieve(f'https://github.com/danielgatis/rembg/releases/download/v0.0.0/{m}.onnx', p)
    img = cv2.imread(photo).astype(np.float32)
    rgb = img[..., ::-1] / 255; Hh, Ww = rgb.shape[:2]

    def run(model, s, mean, std):
        sess = ort.InferenceSession(str(MODELS / f'{model}.onnx'), providers=['CPUExecutionProvider'])
        x = cv2.resize(rgb, (s, s), interpolation=cv2.INTER_LANCZOS4)
        x = ((x / max(x.max(), 1e-6) - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
        o = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
        o = (o - o.min()) / (o.max() - o.min())
        return cv2.resize(o, (Ww, Hh), interpolation=cv2.INTER_LANCZOS4)
    a = run('isnet-general-use', 1024, np.array([.5, .5, .5]), np.array([1., 1, 1]))
    b = run('u2net_human_seg', 320, np.array([.485, .456, .406]), np.array([.229, .224, .225]))
    be = cv2.GaussianBlur(cv2.erode((b > 0.6).astype(np.uint8), np.ones((15, 15), np.uint8)).astype(np.float32), (0, 0), 3)
    al = np.clip((np.clip(np.maximum(a, be), 0, 1) - 0.08) / 0.84, 0, 1)
    g = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = np.clip((g - 128) * 1.18 + 132, 0, 255)                       # black & white, a bit of contrast
    ys, xs = np.where(al > 0.5); fy0, fx0, fx1 = ys.min(), xs.min(), xs.max()
    S = size
    sc = (S * 0.60) / (Hh - fy0)                                        # figure = 60% of board height
    nw, nh = int(round(Ww * sc)), int(round(Hh * sc))
    g2 = cv2.resize(g, (nw, nh), interpolation=cv2.INTER_LANCZOS4); a2 = cv2.resize(al, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
    ox = S // 2 - int((fx0 + fx1) / 2 * sc); oy = S - nh
    O = np.zeros((S, S, 4), np.float32)
    xa, xb = max(0, ox), min(S, ox + nw); ya = max(0, oy)
    O[ya:S, xa:xb, :3] = g2[ya - oy:S - oy, xa - ox:xb - ox, None]
    O[ya:S, xa:xb, 3] = np.clip(a2[ya - oy:S - oy, xa - ox:xb - ox], 0, 1)
    # name: clean heavy white text, thin dark outline, soft shadow
    fp = '/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc'
    font = ImageFont.truetype(fp, int(S * 0.0694), index=0) if os.path.exists(fp) else \
        ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', int(S * 0.0694))
    sw = max(2, int(S * 0.0046))
    T = Image.new('RGBA', (S, S), (0, 0, 0, 0)); dr = ImageDraw.Draw(T)
    bb = dr.textbbox((0, 0), name, font=font, stroke_width=sw)
    while bb[2] - bb[0] > S * 0.92:                                     # long names: shrink
        font = ImageFont.truetype(font.path, int(font.size * 0.93), index=0) if font.path.endswith('ttc') else ImageFont.truetype(font.path, int(font.size * 0.93))
        bb = dr.textbbox((0, 0), name, font=font, stroke_width=sw)
    tx = (S - (bb[2] - bb[0])) // 2 - bb[0]; ty = S - int(S * 0.032) - (bb[3] - bb[1]) - bb[1]
    sh = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((tx, ty + sw), name, font=font, fill=(0, 0, 0, 200), stroke_width=int(sw * 1.4), stroke_fill=(0, 0, 0, 200))
    sh = sh.filter(ImageFilter.GaussianBlur(sw * 1.4))
    dr.text((tx, ty), name, font=font, fill=(255, 255, 255, 255), stroke_width=sw, stroke_fill=(18, 18, 18, 255))
    Tn = np.array(Image.alpha_composite(sh, T)).astype(np.float32)
    ta = Tn[..., 3:] / 255; pa = O[..., 3:]
    alpha = ta + pa * (1 - ta)
    rgbo = (Tn[..., :3][..., ::-1] * ta + O[..., :3] * pa * (1 - ta)) / np.maximum(alpha, 1e-6)
    return rgbo, alpha


def rrect(img, x0, y0, x1, y1, r, col):
    ss = 4; w, h = x1 - x0, y1 - y0
    m = np.zeros((h * ss, w * ss), np.uint8); R = r * ss
    cv2.rectangle(m, (R, 0), (w * ss - R - 1, h * ss - 1), 255, -1); cv2.rectangle(m, (0, R), (w * ss - 1, h * ss - R - 1), 255, -1)
    for cx, cy in [(R, R), (w * ss - R - 1, R), (R, h * ss - R - 1), (w * ss - R - 1, h * ss - R - 1)]: cv2.circle(m, (cx, cy), R, 255, -1)
    a = cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    reg = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (reg * (1 - a[..., None]) + np.array(col, np.float32) * a[..., None]).round().astype(np.uint8)


def to_bgr(M0):
    M = np.zeros((3, 4), np.float32)
    for i, ci in enumerate([2, 1, 0]):
        for j, cj in enumerate([2, 1, 0]): M[i, j] = M0[cj, ci]
        M[i, 3] = M0[3, ci]
    return M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rec'); ap.add_argument('photo'); ap.add_argument('name'); ap.add_argument('out')
    ap.add_argument('--land', type=int); ap.add_argument('--track', default=str(pathlib.Path.home() / 'dbv_songs' / 'f2track.m4a'))
    ap.add_argument('--preview')
    A = ap.parse_args()
    cap = cv2.VideoCapture(A.rec); cap.set(cv2.CAP_PROP_POS_FRAMES, 30); ok, f30 = cap.read()
    x0, y0, x1, y1 = board_bbox(f30)
    size = ((x1 - x0) + (y1 - y0)) // 2
    bx0, by0 = x0 + 1, y0 + 1; bsz = size - 2                           # 1 px inset (antialiased edge)
    land, nrec = (A.land, int(cap.get(cv2.CAP_PROP_FRAME_COUNT))) if A.land else find_land(A.rec, (x0, y0, x1, y1))
    OFF = LAND - land
    print(f'board {x0},{y0},{x1},{y1}  land frame {land} ({land/60:.2f}s)  offset {OFF}', flush=True)
    barh = int(round(size * 0.089)); bscale = 234 / barh
    bc0, bc1 = max(0, x0 - 6), min(f30.shape[1], x0 + int(size * 0.65))
    bot_rows = (y1 + 12, min(f30.shape[0], y1 + 12 + barh))
    top_rows = (max(0, y0 - 10 - barh), max(1, y0 - 10))

    base = np.zeros((H, W, 3), np.uint8); base[:] = BG
    rrect(base, *TP, 28, PANEL); rrect(base, *BP, 28, PANEL); base = base.astype(np.float32)
    R = 12; bm = np.zeros((BS * 4, BS * 4), np.uint8)
    cv2.rectangle(bm, (R, 0), (BS * 4 - R, BS * 4), 255, -1); cv2.rectangle(bm, (0, R), (BS * 4, BS * 4 - R), 255, -1)
    for cx, cy in [(R, R), (BS * 4 - R, R), (R, BS * 4 - R), (BS * 4 - R, BS * 4 - R)]: cv2.circle(bm, (cx, cy), R, 255, -1)
    board_a = (cv2.resize(bm, (BS, BS), interpolation=cv2.INTER_AREA).astype(np.float32) / 255)[..., None]
    BM = to_bgr(np.load(HERE / 'baseM.npy'))                            # chess.com colours -> template look
    GM = np.load(HERE / 'gradeM.npy').astype(np.float32)                # template look -> dark brilliant grade

    def grade(img):
        X = np.clip(img, 0, 255) / 255; B0, G0, R0 = X[..., 0], X[..., 1], X[..., 2]
        F = np.stack([B0, G0, R0, B0 * B0, G0 * G0, R0 * R0, B0 * G0, G0 * R0, B0 * R0, np.ones_like(B0)], -1)
        return F @ GM

    orgb, oa = build_overlay(A.photo, A.name, BS)

    def player(crop, img, panel):
        c = crop.astype(np.float32)
        bgc = np.median(c[:, -60:].reshape(-1, 3), 0)
        big = cv2.resize(cv2.transform(c, BM), None, fx=bscale, fy=bscale, interpolation=cv2.INTER_CUBIC)
        raw = cv2.resize(c, None, fx=bscale, fy=bscale, interpolation=cv2.INTER_CUBIC)
        a = np.clip((np.abs(raw - bgc).max(2) - 6) / 40, 0, 1)[..., None]
        px0, py0, px1, py1 = panel; h, w = big.shape[:2]
        h = min(h, py1 - py0 - 8); xx = px0 + 30; yy = py0 + (py1 - py0 - h) // 2; w = min(w, px1 - 10 - xx)
        img[yy:yy + h, xx:xx + w] = img[yy:yy + h, xx:xx + w] * (1 - a[:h, :w]) + big[:h, :w] * a[:h, :w]

    # snow: soft matte flakes, 3 depths, sway + wind, no glow
    rng = np.random.default_rng(7); N = 300
    z = rng.random(N) ** 1.3; sx = rng.random(N) * W; sy = rng.random(N) * H
    rad = 3.5 + 20 * z ** 1.7; vy = (110 + 380 * z) / FPS; vx = (18 + 40 * z) / FPS
    amp = 8 + 34 * rng.random(N); fq = (0.25 + 0.6 * rng.random(N)) * 2 * np.pi / FPS; ph = rng.random(N) * 2 * np.pi
    op = 0.45 + 0.4 * z; soft = 0.6 + 0.9 * z

    def snow(img, t):
        env = min(1.0, t / 24.0); env = env * env * (3 - 2 * env)
        acc = np.zeros((H, W), np.float32)
        ys = (sy + vy * t) % (H + 120) - 60; xs = (sx + vx * t + amp * np.sin(fq * t + ph)) % (W + 120) - 60
        for i in range(N):
            s = rad[i] * 0.55 * soft[i]; rr = int(np.ceil(rad[i] * 1.2 + 3 * s)) + 2
            cx, cy = xs[i], ys[i]
            X0, X1, Y0, Y1 = max(0, int(cx) - rr), min(W, int(cx) + rr + 1), max(0, int(cy) - rr), min(H, int(cy) + rr + 1)
            if X0 >= X1 or Y0 >= Y1: continue
            gx = np.arange(X0, X1, dtype=np.float32) - cx; gy = np.arange(Y0, Y1, dtype=np.float32)[:, None] - cy
            dd = np.sqrt(gx * gx + gy * gy)
            a = np.clip((rad[i] - dd) / (s + 0.6) + 0.5, 0, 1) * op[i] * env
            acc[Y0:Y1, X0:X1] = 1 - (1 - acc[Y0:Y1, X0:X1]) * (1 - a)
        a = acc[..., None]
        return img * (1 - a) + np.array([238, 238, 236], np.float32) * a

    cap = cv2.VideoCapture(A.rec); cur = [-1, None]
    def getf(r):
        if r < cur[0]: cap.set(cv2.CAP_PROP_POS_FRAMES, r); cur[0] = r - 1
        while cur[0] < r:
            ok, f = cap.read()
            if not ok: break
            cur[0] += 1
            cur[1] = (f[by0:by0 + bsz, bx0:bx0 + bsz].copy(), f[top_rows[0]:top_rows[1], bc0:bc1].copy(), f[bot_rows[0]:bot_rows[1], bc0:bc1].copy())
        return cur[1]

    def ease(t): t = min(max(t, 0), 1); return t * t * (3 - 2 * t)
    ks = [int(x) for x in A.preview.split(',')] if A.preview else range(NOUT)
    p = None
    if not A.preview:
        cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', '60', '-i', '-', '-i', A.track,
               '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-preset', 'medium', '-crf', '14', '-profile:v', 'high',
               '-x264-params', 'keyint=120:min-keyint=60', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '44100', '-ac', '2',
               '-movflags', '+faststart', '-t', f'{NOUT/60:.4f}', A.out]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for k in ks:
        fr = getf(min(max(k - OFF, 0), nrec - 1))
        img = base.copy()
        b = cv2.transform(cv2.resize(fr[0], (BS, BS), interpolation=cv2.INTER_CUBIC).astype(np.float32), BM)
        if k >= GRADE_AT: b = grade(b)
        a = 0.0
        if FIN0 <= k < FOUT1:
            a = ease((k - FIN0) / (FIN1 - FIN0)) if k < FIN1 else (1.0 if k < FOUT0 else 1 - ease((k - FOUT0) / (FOUT1 - FOUT0)))
        if a > 0:
            sc = 0.94 + 0.06 * a
            if sc < 0.999:
                Mw = np.float32([[sc, 0, BS / 2 * (1 - sc)], [0, sc, BS * (1 - sc)]])
                o4 = cv2.warpAffine(np.dstack([orgb * oa, oa]), Mw, (BS, BS), flags=cv2.INTER_LINEAR, borderValue=0)
                pa, prgb = o4[..., 3:], o4[..., :3]
            else: pa, prgb = oa, orgb * oa
            b = b * (1 - pa * a) + prgb * a
        img[BY:BY + BS, BX:BX + BS] = img[BY:BY + BS, BX:BX + BS] * (1 - board_a) + b * board_a
        player(fr[1], img, TP); player(fr[2], img, BP)
        if k >= GRADE_AT: img = snow(img, k - GRADE_AT)
        o = np.clip(img, 0, 255).astype(np.uint8)
        if A.preview: cv2.imwrite(f'{A.out}_{k}.png', o)
        else: p.stdin.write(o.tobytes())
    if p: p.stdin.close(); p.wait(); print('DONE', A.out)


if __name__ == '__main__':
    main()
