import cv2, numpy as np, subprocess, sys, os
# usage: REC=... python3 render.py F0 F1 out.mp4 audio.mp3 audio_offset
REC = os.environ['REC']
F0, F1 = int(sys.argv[1]), int(sys.argv[2])
out, audio, aoff = sys.argv[3], sys.argv[4], float(sys.argv[5])
YK = float(os.environ.get('YELLOW', '0.92'))
W, H = 2160, 3840
BG = (25, 29, 31); PANEL = (20, 22, 24)

# --- recording geometry (2026-10 chess.com layout) ---
RB = (400, 76, 1300, 976)          # board in recording
XL, XR = 392, 1306                 # window content columns
TOPROWS = (0, 74); BOTROWS = (979, 1062)
PX0, PX1 = 394, 858                # player strip columns
AV_X = 399; AV_TOP_Y = 0; AV_BOT_Y = 989

# colour grade fitted to sample videos (BGR, 3x4)
CM = np.array([[1.2618, -0.1774, -0.0032, -18.2132],
               [-0.3253, 1.1992, 0.2290, -22.6035],
               [-0.1446, -0.0665, 1.3186, -25.3378]], np.float32)
YREF = [np.array(c, np.float32) for c in [(64, 168, 180), (128, 212, 240), (49, 198, 247)]]

def rrect(img, x0, y0, x1, y1, r, col):
    ss = 4; w, h = x1 - x0, y1 - y0
    m = np.zeros((h * ss, w * ss), np.uint8); R = r * ss
    cv2.rectangle(m, (R, 0), (w * ss - R - 1, h * ss - 1), 255, -1)
    cv2.rectangle(m, (0, R), (w * ss - 1, h * ss - R - 1), 255, -1)
    for cx, cy in [(R, R), (w * ss - R - 1, R), (R, h * ss - R - 1), (w * ss - R - 1, h * ss - R - 1)]:
        cv2.circle(m, (cx, cy), R, 255, -1)
    a = cv2.resize(m, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    reg = img[y0:y1, x0:x1].astype(np.float32)
    img[y0:y1, x0:x1] = (reg * (1 - a[..., None]) + np.array(col, np.float32) * a[..., None]).round().astype(np.uint8)

base = np.zeros((H, W, 3), np.uint8); base[:] = BG
TP = (37, 520, 2126, 781); BP = (37, 2925, 2126, 3182)
rrect(base, *TP, 28, PANEL); rrect(base, *BP, 28, PANEL)
BX, BY, BS = 36, 804, 2090
R = 12; bm = np.zeros((BS * 4, BS * 4), np.uint8)
cv2.rectangle(bm, (R, 0), (BS * 4 - R, BS * 4), 255, -1); cv2.rectangle(bm, (0, R), (BS * 4, BS * 4 - R), 255, -1)
for cx, cy in [(R, R), (BS * 4 - R, R), (R, BS * 4 - R), (BS * 4 - R, BS * 4 - R)]:
    cv2.circle(bm, (cx, cy), R, 255, -1)
board_a = (cv2.resize(bm, (BS, BS), interpolation=cv2.INTER_AREA).astype(np.float32) / 255)[..., None]

def player(fr, img, ry0, ry1, sy0, panel):
    crop = fr[ry0:ry1, PX0:PX1].astype(np.float32)
    bg = np.median(crop[:, -40:].reshape(-1, 3), 0)
    big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    a = np.clip((np.abs(big - bg).max(2) - 6) / 40, 0, 1)[..., None]
    x0 = 3 * (PX0 - AV_X) + 84; y0 = sy0
    h, w = big.shape[:2]
    px0, py0, px1, py1 = panel
    X0 = max(x0, px0 + 4); Y0 = max(y0, py0 + 4); X1 = min(x0 + w, px1 - 10); Y1 = min(y0 + h, py1 - 4)
    sub = big[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]; aa = a[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
    reg = img[Y0:Y1, X0:X1].astype(np.float32)
    img[Y0:Y1, X0:X1] = np.clip(reg * (1 - aa) + sub * aa, 0, 255).astype(np.uint8)

cap = cv2.VideoCapture(REC); cap.set(cv2.CAP_PROP_POS_FRAMES, F0)
dur = (F1 - F0) / 60
cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', '60', '-i', '-',
       '-ss', str(aoff), '-t', f'{dur:.4f}', '-i', audio]
GV = os.environ.get('GAMEVOL')
if GV:
    cmd += ['-ss', f'{F0/60:.4f}', '-t', f'{dur:.4f}', '-i', REC, '-filter_complex',
            f'[1:a]aresample=44100,aformat=channel_layouts=stereo[m];[2:a]aresample=44100,aformat=channel_layouts=stereo,volume={GV}[g];[m][g]amix=inputs=2:duration=first:normalize=0[a]',
            '-map', '0:v', '-map', '[a]']
else:
    cmd += ['-map', '0:v', '-map', '1:a']
cmd += [
       '-c:v', 'libx264', '-preset', 'medium', '-crf', '14', '-profile:v', 'high', '-x264-params', 'keyint=120:min-keyint=60',
       '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '44100', '-ac', '2', '-movflags', '+faststart', '-t', f'{dur:.4f}', out]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
n = 0
by0, by1, bx0, bx1 = RB[1], RB[3], RB[0], RB[2]
for i in range(F0, F1):
    ok, fr = cap.read()
    if not ok: break
    raw = fr[:, XL:XR].astype(np.float32)
    g = cv2.transform(raw, CM)
    if YK < 1.0:
        sub = raw[by0:by1, bx0 - XL:bx1 - XL]
        w = np.zeros(sub.shape[:2], np.float32)
        for c in YREF:
            d = np.abs(sub - c).max(2); w = np.maximum(w, np.clip(1 - (d - 18) / 24, 0, 1))
        g[by0:by1, bx0 - XL:bx1 - XL] *= (1 - (1 - YK) * w)[..., None]
    fr = fr.copy(); fr[:, XL:XR] = np.clip(g, 0, 255).astype(np.uint8)
    img = base.copy()
    b = cv2.resize(fr[by0:by1, bx0:bx1], (BS, BS), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    reg = img[BY:BY + BS, BX:BX + BS].astype(np.float32)
    img[BY:BY + BS, BX:BX + BS] = (reg * (1 - board_a) + b * board_a).round().astype(np.uint8)
    player(fr, img, TOPROWS[0], TOPROWS[1], 556 + 3 * (TOPROWS[0] - AV_TOP_Y), TP)
    player(fr, img, BOTROWS[0], BOTROWS[1], 2962 + 3 * (BOTROWS[0] - AV_BOT_Y), BP)
    if n == 0 and os.environ.get('PREVIEW'): cv2.imwrite(os.environ['PREVIEW'], img)
    p.stdin.write(img.tobytes()); n += 1
p.stdin.close(); p.wait(); print('frames', n)
