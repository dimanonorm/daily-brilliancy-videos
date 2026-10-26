"""Telegram bot: receive chess.com review recordings, make Shorts, reply, push drafts/uploads.

Runs inside GitHub Actions every few minutes (no server needed).
"""
import os, sys, json, time, html, pathlib, subprocess, tempfile, traceback, datetime, requests

ENGINE = pathlib.Path(__file__).resolve().parent
ROOT = ENGINE.parent
sys.path.insert(0, str(ENGINE))
import pipeline, titles

STATE = ROOT / "state"; INBOX = ROOT / "inbox"; VIDEOS = ROOT / "videos"
TOKEN = os.environ["TG_BOT_TOKEN"].strip()
API = f"https://api.telegram.org/bot{TOKEN}"
SONGDIR = pathlib.Path(tempfile.gettempdir()) / "songs"

def load(name, default):
    p = STATE / name
    return json.loads(p.read_text()) if p.exists() else default

def save(name, data):
    (STATE / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

def git(msg):
    paths = [p for p in ("state", "inbox", "videos") if (ROOT / p).exists()]
    subprocess.run(["git", "add", "-A", "--"] + paths, cwd=ROOT)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
        subprocess.run(["git", "commit", "-qm", msg], cwd=ROOT, check=True)
        for _ in range(3):
            subprocess.run(["git", "pull", "--rebase", "-q"], cwd=ROOT)
            if subprocess.run(["git", "push", "-q"], cwd=ROOT).returncode == 0: return
            time.sleep(5)

def tg(method, **kw):
    files = kw.pop("files", None)
    r = requests.post(f"{API}/{method}", data=kw, files=files, timeout=300)
    j = r.json()
    if not j.get("ok"): print("TG ERROR", method, j, flush=True)
    return j.get("result")

def say(chat, text):
    return tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview="true")

def fetch_updates(cfg):
    if "owner" in cfg:  # migrate old single-owner format
        o = cfg.pop("owner"); cfg.setdefault("owners", [])
        if o and o not in cfg["owners"]: cfg["owners"].append(o)
    owners = cfg.setdefault("owners", [])
    # roles: owners[0] sends recordings; "receiver" (main account) gets the finished videos
    if not cfg.get("receiver") and len(owners) >= 2:
        cfg["receiver"] = owners[1]
    maxo = cfg.get("max_owners", 2)
    upd = tg("getUpdates", offset=cfg.get("offset", 0), timeout=0) or []
    jobs = []
    for u in upd:
        cfg["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        chat = m.get("chat", {}).get("id")
        if not chat: continue
        text = m.get("text", "")
        if chat not in owners and text.startswith("/start") and len(owners) < maxo:
            owners.append(chat)
            if len(owners) == 1:
                say(chat, "✅ Готово! С этого аккаунта присылай записи разбора chess.com (лучше <b>как файл</b>).")
            else:
                cfg["receiver"] = chat
                say(chat, "✅ Готово! Это твой основной аккаунт: сюда будут приходить готовые Shorts, названия и хэштеги.")
                say(owners[0], "🔗 Основной аккаунт подключён — готовые видео теперь будут приходить туда.")
            continue
        if chat not in owners:
            say(chat, "Это приватный бот."); continue
        f = m.get("document") or m.get("video")
        if not f:
            if text.startswith("/start"):
                say(chat, "Я на связи. Присылай записи разбора — лучше как файл.")
            elif text:
                say(chat, "Присылай записи разбора (видео-файлы .mkv/.mp4). Текст я не обрабатываю.")
            continue
        if f.get("file_size", 0) > 20 * 1024 * 1024:
            say(chat, f"⚠️ Файл {html.escape(f.get('file_name','video'))} больше 20 МБ — Telegram не даёт ботам скачивать такие. Обрежь запись или пришли поменьше.")
            continue
        info = tg("getFile", file_id=f["file_id"])
        if not info: continue
        name = f.get("file_name") or f"video_{m['message_id']}.mp4"
        data = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", timeout=300).content
        INBOX.mkdir(exist_ok=True)
        stamp = datetime.datetime.utcfromtimestamp(m["date"]).strftime("%Y%m%d-%H%M%S")
        dst = INBOX / f"{stamp}-{m['message_id']}-{''.join(c for c in name if c.isalnum() or c in '._-')}"
        dst.write_bytes(data)
        senders = load("inbox_senders.json", {}); senders[dst.name] = chat; save("inbox_senders.json", senders)
        jobs.append(dst.name)
    return jobs

def decrypt_song(fname):
    SONGDIR.mkdir(exist_ok=True)
    out = SONGDIR / fname
    if not out.exists():
        subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:SONGS_KEY",
                        "-in", str(ROOT / "songs_enc" / (fname + ".enc")), "-out", str(out)], check=True)
    return str(out)

def pick_song():
    songs = {s["file"]: s for s in json.loads((ENGINE / "songs.json").read_text())}
    lru = [f for f in load("songs_lru.json", list(songs)) if f in songs] + [f for f in songs if f not in load("songs_lru.json", [])]
    f = lru[0]; lru = lru[1:] + [f]; save("songs_lru.json", lru)
    return songs[f]

def snap(camel, known):
    import difflib
    best = difflib.get_close_matches(camel, known, n=1, cutoff=0.85)
    return best[0] if best else camel

def display_fix(display, known):
    import re
    c = pipeline.camel(display); s = snap(c, known)
    return display if s == c else re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)

def unique_name(white, black):
    pairs = load("pairs.json", {})
    known = sorted({p for k in pairs for p in k.split(" vs ")})
    base = f"{snap(pipeline.camel(white), known)} vs {snap(pipeline.camel(black), known)}"
    n = pairs.get(base, 0) + 1; pairs[base] = n; save("pairs.json", pairs)
    return base if n == 1 else f"{base} {n}"

def slugify(s):
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-").replace("--", "-")

def process_inbox(chat, files):
    if not files: return []
    say(chat, f"🎬 Принял записей: {len(files)}. Делаю видео…")
    done = []
    counter = load("title_counter.json", {"n": 0})
    for p in files:
        try:
            song = pick_song()
            with tempfile.TemporaryDirectory() as wd:
                r = pipeline.process(str(p), decrypt_song(song["file"]), song["move_at"], wd)
                known = sorted({x for k in load("pairs.json", {}) for x in k.split(" vs ")})
                wd_, bd_ = display_fix(r["white"], known), display_fix(r["black"], known)
                name = unique_name(r["white"], r["black"])
                title, tags = titles.make(wd_, bd_, counter["n"]); counter["n"] += 1
                save("title_counter.json", counter)
                slug = slugify(name)
                VIDEOS.mkdir(exist_ok=True)
                mp4 = VIDEOS / f"{slug}.mp4"
                subprocess.run(["cp", r["out"], str(mp4)], check=True)
                caption = f"Why is this move brilliant? {title} {tags}"
                (VIDEOS / f"{slug}.json").write_text(json.dumps({
                    "title": title, "description": tags, "hashtags": tags, "tags": [t[1:] for t in tags.split()],
                    "tiktok_caption": caption, "game": name, "song": song["name"]}, indent=2, ensure_ascii=False) + "\n")
            p.unlink(); senders = load("inbox_senders.json", {}); senders.pop(p.name, None); save("inbox_senders.json", senders)
            git(f"Bot: {name}")
            with open(mp4, "rb") as fh:
                tg("sendVideo", chat_id=chat, caption=f"{name} (youtube)", supports_streaming="true",
                   files={"video": (f"{name} (youtube).mp4", fh, "video/mp4")})
            ok = r["verified_s"] is not None and abs(r["verified_s"] - r["move_s"]) < 0.05
            say(chat, f"<b>{html.escape(name)}</b> · 🎵 {html.escape(song['name'])}"
                      f"{'' if ok else ' · ⚠️ проверь момент хода'}\n\n"
                      f"Название:\n<code>{html.escape(title)}</code>\n\nОписание:\n<code>{html.escape(tags)}</code>\n\n"
                      f"TikTok:\n<code>{html.escape(caption)}</code>")
            done.append(name)
        except Exception as e:
            traceback.print_exc()
            failed = INBOX / "failed"; failed.mkdir(exist_ok=True)
            if p.exists(): p.rename(failed / p.name)
            git(f"Bot: failed {p.name}")
            say(chat, f"⚠️ Не получилось сделать видео из <code>{html.escape(p.name)}</code>: {html.escape(str(e))}")
    return done

def main():
    cfg = load("telegram.json", {})
    fetch_updates(cfg)
    save("telegram.json", cfg)
    git("Bot: inbox")
    owners = cfg.get("owners", [])
    if not owners: print("No owner yet (send /start to the bot)."); return
    files = sorted(p for p in INBOX.glob("*") if p.is_file()) if INBOX.exists() else []
    senders = load("inbox_senders.json", {})
    by_chat = {}
    for p in files: by_chat.setdefault(senders.get(p.name, owners[0]), []).append(p)
    cfgp = json.loads((ROOT / "config.json").read_text())
    recv = cfg.get("receiver")
    for chat, fs in by_chat.items():
        if recv and chat != recv:
            say(chat, f"🎬 Принял записей: {len(fs)}. Готовые видео пришлю в основной аккаунт.")
    results = {}
    for chat, fs in by_chat.items():
        target = recv or chat
        results.setdefault(target, []).extend(process_inbox(target, fs))
    if any(results.values()):
        env = dict(os.environ)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "yt.py")], cwd=ROOT, env=env)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "tiktok.py"), "publish"], cwd=ROOT, env=env)
        git("Bot: publish state")
    tt = load("tiktok_uploaded.json", {})
    for chat, done in results.items():
        if not done: continue
        sent = sum(1 for n in done if slugify(n) in tt)
        msg = f"✅ Готово, видео: {len(done)}\n\n<code>{html.escape(chr(10).join(done))}</code>\n\n"
        if cfgp.get("tiktok_mode") == "inbox": msg += f"📱 В TikTok черновиками отправлено: {sent}. Открой TikTok → вставь подпись → «Опубликовать».\n"
        if not cfgp.get("youtube_upload"): msg += "▶️ YouTube: загрузи файлы выше (название и хэштеги — в сообщениях). Комментарий добавится сам."
        say(chat, msg)

if __name__ == "__main__":
    main()
