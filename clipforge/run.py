#!/usr/bin/env python3
"""ClipForge GTA - server gratuito su GitHub Actions.

4 agenti:
  Scout  - controlla gli streamer della lista e vede chi e' in live su GTA
  Hunter - registra la live e trova i picchi (urla nell'audio + azione nel gioco)
  Judge  - da' un voto di viralita' 0-100 e tiene solo le clip migliori
  Editor - monta clip 9:16 (facecam sopra, gioco sotto), titolo piccolo in inglese,
           teaser iniziale, credito twitch.tv/streamer, barra di avanzamento
Le clip finiscono nella galleria (ramo gh-pages) da aprire sull'iPhone.
"""
import json, math, os, re, shutil, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "_work")
PAGES = os.path.join(ROOT, "_pages")
FONT_B = os.path.join(ROOT, "fonts", "Inter-Bold.otf")
FONT_S = os.path.join(ROOT, "fonts", "Inter-SemiBold.otf")
CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
OUT_W, OUT_H, SPLIT = 1080, 1920, 0.4
CAM_PRESETS = {"tl": [0.01, 0.02, 0.24, 0.30], "tr": [0.75, 0.02, 0.24, 0.30],
               "bl": [0.01, 0.66, 0.24, 0.30], "br": [0.74, 0.66, 0.24, 0.30]}


def log(*a):
    print("[ClipForge]", *a, flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


# ----------------------------------------------------------------- Scout
def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request("https://gql.twitch.tv/gql", data=body,
                                 headers={"Client-ID": CID, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def live_info(login):
    """Ritorna (in_live, nome_gioco, titolo)."""
    try:
        d = gql("query($l:String!){user(login:$l){login stream{id title game{name}}}}", {"l": login})
        user = (d.get("data") or {}).get("user")
        if user is not None:
            st = user.get("stream")
            if not st:
                return False, "", ""
            return True, ((st.get("game") or {}).get("name") or ""), st.get("title") or ""
    except Exception as e:
        log("GQL non disponibile per", login, "->", e)
    # piano B: chiedo a streamlink se ci sono stream
    try:
        out = subprocess.run(["streamlink", "--json", f"https://www.twitch.tv/{login}"],
                             capture_output=True, text=True, timeout=60).stdout
        return bool(json.loads(out or "{}").get("streams")), "", ""
    except Exception:
        return False, "", ""


def top_gta_live(n=3):
    """Gli streamer in live su GTA V con piu' spettatori in questo momento."""
    try:
        d = gql('query($n:Int){game(name:"Grand Theft Auto V"){streams(first:$n){edges{node{viewersCount title '
                'broadcaster{login}}}}}}', {"n": n})
        edges = (((d.get("data") or {}).get("game") or {}).get("streams") or {}).get("edges") or []
        latin = lambda t: (sum(ch.isascii() for ch in t) / max(1, len(t))) > 0.85
        return [(e["node"]["broadcaster"]["login"].lower(), e["node"].get("viewersCount", 0)) for e in edges
                if e.get("node") and e["node"].get("broadcaster") and latin(e["node"].get("title") or "")]
    except Exception as e:
        log("Scoperta live GTA non riuscita:", e)
        return []


def is_gta(game, title):
    t = f"{game} {title}".lower()
    return "grand theft auto" in t or re.search(r"\bgta\b|\bgtarp\b|nopixel", t) is not None


# ----------------------------------------------------------------- Hunter: registrazione
QUALITY = "1080p60,1080p,936p60,900p60,720p60,720p,best"


def record(url, minutes, start_min, out):
    cmd = ["streamlink", "--twitch-disable-ads", "--retry-open", "3", "--retry-streams", "5",
           "--stream-segment-threads", "3", "--hls-duration", f"{int(minutes*60)}", "-f", "-o", out]
    if start_min:
        cmd += ["--hls-start-offset", f"{int(start_min*60)}"]
    cmd += [url, QUALITY]
    log("Registro:", url, f"{minutes} min" + (f" dal minuto {start_min}" if start_min else ""))
    try:
        subprocess.run(cmd, check=False, timeout=minutes*60 + 600)
    except subprocess.TimeoutExpired:
        log("Registrazione oltre il tempo previsto: uso quello che c'e'")
    ok = os.path.exists(out) and os.path.getsize(out) > 2_000_000
    if not ok:
        log("Niente di utile registrato da", url)
    return ok


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", path],
                         capture_output=True, text=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return int(s["width"]), int(s["height"]), float(j["format"]["duration"])


# ----------------------------------------------------------------- Hunter: analisi
def audio_db(path, n):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000",
                          "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    out = np.full(n, -90.0)
    for s in range(n):
        seg = a[s*8000:(s+1)*8000]
        if len(seg):
            out[s] = max(-90.0, 20*math.log10(math.sqrt(float(np.mean(seg**2))) + 1e-9))
    return out


def motion(path, n, cam):
    W, H = 48, 27
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf", f"fps=1,scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W).astype(np.float32)
    mask = np.ones((H, W), bool)
    if cam:
        x, y, w, h = cam
        mask[int(y*H):int(math.ceil((y+h)*H)), int(x*W):int(math.ceil((x+w)*W))] = False
    mot, cx = np.zeros(n), np.full(n, 0.5)
    xs = (np.arange(W) + 0.5) / W
    for i in range(1, min(n, len(fr))):
        d = np.abs(fr[i] - fr[i-1]) * mask
        mot[i] = d.sum() / mask.sum() / 255
        big = np.where(d > 18, d, 0)
        if big.sum() > 0:
            cx[i] = float((big.sum(axis=0) * xs).sum() / big.sum())
    return mot, cx


def smooth(x, w=3):
    k = np.ones(w) / w
    return np.convolve(np.pad(x, (w//2, w//2), mode="edge"), k, mode="valid")[:len(x)]


def robust_z(x):
    md = np.median(x)
    mad = np.median(np.abs(x - md)) * 1.4826
    if mad < 1e-6:
        sd = x.std()
        return np.zeros_like(x) if sd < 1e-6 else np.maximum(0, (x - x.mean()) / sd)
    return np.maximum(0, (x - md) / mad)


def baseline_z(x, w=60):
    n, h = len(x), w // 2
    d = np.array([x[i] - np.median(x[max(0, i-h):min(n, i+h+1)]) for i in range(n)])
    mad = np.median(np.abs(d)) * 1.4826 or 1
    return np.maximum(0, d / mad)


def candidates(score, max_n, length, dur):
    n, pre = len(score), round(length * 0.62)
    taken, picks = np.zeros(n, bool), []
    order = np.argsort(-score)
    min_s = max(0.8, 0.2 * score[order[0]])
    for i in order:
        if len(picks) >= max_n:
            break
        if taken[i] or (score[i] < min_s and picks):
            continue
        start = max(0, i - pre)
        end = min(dur, start + length)
        start = max(0, end - length)
        if end - start < min(6, dur):
            continue
        picks.append({"peak": int(i), "start": float(start), "end": float(end), "score": float(score[i])})
        taken[max(0, int(start) - 3):min(n, int(math.ceil(end)) + 3)] = True
    return picks


# ----------------------------------------------------------------- Judge
def judge(c, zA, zS, score, mx):
    n = len(score)
    a, b = int(c["start"]), min(n, int(math.ceil(c["end"])))
    win = score[a:b]
    mean = float(win.mean()) if len(win) else 0
    near = [i for i in range(c["peak"]-4, c["peak"]+5) if 0 <= i < n]
    pa, ps = float(max(zA[near])), float(max(zS[near]))
    has_a, has_s = pa > 1.2, ps > 1.2
    peaks = int((win > 0.5 * mx).sum())
    pos = (c["peak"] - c["start"]) / max(1, c["end"] - c["start"])
    v = 30 + 38*c["score"]/mx + 12*min(1, mean/(0.45*mx)) + (12 if has_a and has_s else 7 if has_a else 4 if has_s else 0) \
        + min(6, peaks) + (4 if 0.45 < pos < 0.85 else 0)
    pct = lambda x: int(round(max(0, min(1, x)) * 100))
    c.update(viral=int(round(max(5, min(98, v)))), has_a=has_a, has_s=has_s,
             subs={"Hook": pct(0.35 + 0.65*c["score"]/mx), "Azione": pct(ps/4), "Reazione": pct(pa/4),
                   "Ritmo": pct(0.25*min(4, peaks) + 0.6*mean/mx)})
    return c


HOOKS = {
    "rp": {"both": ["This chase went way too far", "He really thought he could get away", "Nobody in the city saw this coming",
                    "The cops did not expect this"],
           "audio": ["Chat could not believe what he said", "He completely lost it here", "This argument got out of hand",
                     "His reaction says everything"],
           "scene": ["Wait for the ending", "The worst getaway in RP history", "This crash was not in the plan"],
           "weak": ["Wait for it...", "This RP moment is wild"]},
    "online": {"both": ["This lobby turned into chaos", "He did not expect that at all", "Worst heist ever?"],
               "audio": ["He lost it over this", "His reaction is everything"],
               "scene": ["Watch until the end", "That was way too close"], "weak": ["Wait for it..."]},
}


def caption(hook, ch, mode):
    extra = ["#gtarp", "#nopixel", "#roleplay"] if mode == "rp" else ["#gtaonline", "#gtaclips"]
    slug = re.sub(r"\W", "", ch.lower())
    tags = [f"#{slug}", f"#{slug}clips"] + extra + \
           ["#gta5", "#gtav", "#twitch", "#twitchclips", "#gaming", "#fyp"]
    return f"{hook} \U0001F633\n\nWould you have done the same? \U0001F447\nFollow for daily GTA clips \U0001F514\n\n" \
           f"\U0001F3A5 twitch.tv/{ch}\n\n" + " ".join(dict.fromkeys(tags))


# ----------------------------------------------------------------- Editor
def wrap(text, width=30):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:2])


def even(v):
    return max(2, int(v) // 2 * 2)


def render(src, sw, shh, clip, cam, ch, teaser, out_mp4, out_jpg, tmp):
    """Monta una clip 9:16 con ffmpeg."""
    split = cam is not None and sw > shh
    top = int(OUT_H * SPLIT) if split else 0
    gh_out = OUT_H - top
    gw = min(sw, even(shh * OUT_W / gh_out))
    cxm = clip.get("cx", 0.5)
    gx = even(max(0, min(sw - gw, cxm * sw - gw / 2)))
    parts = [(clip["peak"] - 0.4, 1.5), (clip["start"], clip["end"] - clip["start"])] \
        if teaser and clip["peak"] - clip["start"] > 4 else [(clip["start"], clip["end"] - clip["start"])]
    total = sum(p[1] for p in parts)
    args, fc = ["ffmpeg", "-v", "error", "-y"], []
    for (ss, d) in parts:
        args += ["-ss", f"{ss:.2f}", "-t", f"{d:.2f}", "-i", src]
    for i in range(len(parts)):
        if split:
            x, y, w, h = cam
            cw, chh, cx0, cy0 = even(w*sw), even(h*shh), even(x*sw), even(y*shh)
            fc.append(f"[{i}:v]split=2[c{i}][g{i}];"
                      f"[c{i}]crop={cw}:{chh}:{cx0}:{cy0},scale={OUT_W}:{top}:force_original_aspect_ratio=increase:flags=lanczos,"
                      f"crop={OUT_W}:{top},setsar=1[cc{i}];"
                      f"[g{i}]crop={gw}:{shh}:{gx}:0,scale={OUT_W}:{gh_out}:flags=lanczos,setsar=1[gg{i}];"
                      f"[cc{i}][gg{i}]vstack,fps=30,format=yuv420p[v{i}]")
        else:
            fc.append(f"[{i}:v]crop={gw}:{shh}:{gx}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,fps=30,format=yuv420p[v{i}]")
        fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    if len(parts) == 2:
        fc.append("[v1]fade=t=in:st=0:d=0.2:color=white[v1f]")
        fc.append("[v0][a0][v1f][a1]concat=n=2:v=1:a=1[vc][ac]")
    else:
        fc.append("[v0]null[vc];[a0]anull[ac]")
    hook_file, ch_file = os.path.join(tmp, "hook.txt"), os.path.join(tmp, "ch.txt")
    with open(hook_file, "w") as f:
        f.write(wrap(clip["hook"]))
    with open(ch_file, "w") as f:
        f.write(f"twitch.tv/{ch}")
    nl = wrap(clip["hook"]).count("\n") + 1
    ty = (top if split else 260) - (nl * 56) // 2
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:")
    ov = (f"[vc]" + (f"drawbox=x=0:y={top-2}:w=iw:h=4:color=black:t=fill," if split else "") +
          f"drawtext=fontfile='{esc(FONT_B)}':textfile='{esc(hook_file)}':fontsize=42:fontcolor=0x111111:"
          f"line_spacing=12:text_align=C:box=1:boxcolor=white:boxborderw=24:x=(w-text_w)/2:y={ty}:alpha='min(1,t/0.25)',"
          f"drawtext=fontfile='{esc(FONT_S)}':textfile='{esc(ch_file)}':fontsize=30:fontcolor=white:"
          f"box=1:boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y=h-270[vt]")
    fc.append(ov)
    args += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", f"color=c=0xffb21a:s={OUT_W}x10:r=30"]
    bi = len(parts)
    fc.append(f"[vt][{bi}:v]overlay=x='-w+W*t/{total:.2f}':y=H-10:shortest=1[v]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[ac]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_mp4]
    sh(args)
    peak_at = (1.5 if len(parts) == 2 else 0) + (clip["peak"] - clip["start"])
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, peak_at):.2f}", "-i", out_mp4, "-frames:v", "1",
        "-vf", "scale=360:-2", "-q:v", "4", out_jpg])


# ----------------------------------------------------------------- pipeline per una registrazione
def process(src, ch, cam_key, cfg, tag):
    sw, shh, dur = probe(src)
    cam = None if cam_key in (None, "none", "") else (CAM_PRESETS.get(cam_key) if isinstance(cam_key, str) else cam_key)
    n = int(math.ceil(dur)) + 1
    log(f"Hunter: analizzo {ch} ({sw}x{shh}, {dur/60:.1f} min)")
    try:
        aud = audio_db(src, n)
        zA = smooth(baseline_z(aud, 60), 3)
    except Exception as e:
        log("audio non leggibile:", e)
        zA = np.zeros(n)
    mot, cx = motion(src, n, cam)
    zS = smooth(robust_z(mot), 5)
    score = smooth(1.5*zA + 0.55*zS + 0.35*np.minimum(zA, zS), 3)
    score[:3] *= 0.3
    score[-3:] *= 0.3
    mx = max(1e-6, float(score.max()))
    length = min(cfg.get("durata_clip", 30), dur)
    keep = cfg.get("clip_per_live", 2)
    cands = [judge(c, zA, zS, score, mx) for c in candidates(score, max(keep*2 + 2, 6), length, dur)]
    cands.sort(key=lambda c: -c["viral"])
    chosen = [c for i, c in enumerate(cands) if i < keep and (c["viral"] >= cfg.get("voto_minimo", 55) or i == 0)]
    log(f"Judge: {len(cands)} candidati, tengo {len(chosen)} -> " + ", ".join(str(c["viral"]) for c in chosen))
    mode = cfg.get("modo", "rp")
    bank = HOOKS.get(mode, HOOKS["rp"])
    made = []
    for k, c in enumerate(chosen):
        kind = "both" if c["has_a"] and c["has_s"] else "audio" if c["has_a"] else "scene" if c["has_s"] else "weak"
        c["hook"] = bank[kind][(int(time.time()) // 60 + k) % len(bank[kind])]
        a, b = int(c["start"]), int(math.ceil(c["end"]))
        c["cx"] = float(np.clip(0.5 + (np.median(cx[a:b]) - 0.5) * 0.8, 0.2, 0.8)) if cfg.get("segui_azione", True) else 0.5
        cid = f"{tag}_{k}"
        mp4, jpg = os.path.join(WORK, cid + ".mp4"), os.path.join(WORK, cid + ".jpg")
        log(f"Editor: monto clip {k+1}/{len(chosen)} (voto {c['viral']})")
        try:
            render(src, sw, shh, c, cam, ch, cfg.get("teaser", True), mp4, jpg, WORK)
        except subprocess.CalledProcessError as e:
            log("Montaggio fallito:", e)
            continue
        made.append({"id": cid, "file": f"clips/{cid}.mp4", "poster": f"clips/{cid}.jpg", "ch": ch,
                     "hook": c["hook"], "caption": caption(c["hook"], ch, mode), "viral": c["viral"], "subs": c["subs"],
                     "created": int(time.time()), "at": round(c["start"]), "mb": round(os.path.getsize(mp4)/1048576, 1)})
    return made


# ----------------------------------------------------------------- galleria (ramo gh-pages)
def publish(new_clips, status):
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    shutil.rmtree(PAGES, ignore_errors=True)
    if repo and token:
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "gh-pages", url, PAGES])
        if r.returncode != 0:
            os.makedirs(PAGES, exist_ok=True)
    else:
        os.makedirs(PAGES, exist_ok=True)
    os.makedirs(os.path.join(PAGES, "clips"), exist_ok=True)
    db_path = os.path.join(PAGES, "clips.json")
    try:
        clips = json.load(open(db_path))
    except Exception:
        clips = []
    for c in new_clips:
        shutil.copy(os.path.join(WORK, c["id"] + ".mp4"), os.path.join(PAGES, c["file"]))
        shutil.copy(os.path.join(WORK, c["id"] + ".jpg"), os.path.join(PAGES, c["poster"]))
    clips = new_clips + clips
    keep = int(load_cfg().get("tieni_clip", 40))
    for old in clips[keep:]:
        for f in (old["file"], old["poster"]):
            p = os.path.join(PAGES, f)
            if os.path.exists(p):
                os.remove(p)
    clips = clips[:keep]
    json.dump(clips, open(db_path, "w"), ensure_ascii=False, indent=1)
    json.dump(status, open(os.path.join(PAGES, "status.json"), "w"), ensure_ascii=False, indent=1)
    shutil.copy(os.path.join(ROOT, "site", "index.html"), os.path.join(PAGES, "index.html"))
    open(os.path.join(PAGES, ".nojekyll"), "w").close()
    if not (repo and token):
        log("Prova locale: galleria in", PAGES)
        return
    # un solo commit "orfano": la storia non cresce e il repo resta leggero
    shutil.rmtree(os.path.join(PAGES, ".git"), ignore_errors=True)
    g = lambda *a: sh(["git", "-C", PAGES, *a])
    g("init", "-q", "-b", "gh-pages")
    g("config", "user.name", "ClipForge GTA")
    g("config", "user.email", "clipforge@users.noreply.github.com")
    g("add", "-A")
    g("commit", "-q", "-m", f"ClipForge: {len(new_clips)} nuove clip")
    g("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{repo}.git", "gh-pages")
    log("Galleria aggiornata:", len(new_clips), "nuove clip,", len(clips), "in totale")


def load_cfg():
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


def parse_link(s):
    s = s.strip()
    m = re.search(r"twitch\.tv/(?:\w+/)?videos?/(\d+)", s)
    if m:
        return "vod", m.group(1), f"https://www.twitch.tv/videos/{m.group(1)}"
    m = re.search(r"twitch\.tv/(?:popout/)?([A-Za-z0-9_]{2,25})", s) or re.fullmatch(r"@?([A-Za-z0-9_]{2,25})", s)
    if m:
        return "live", m.group(1).lower(), f"https://www.twitch.tv/{m.group(1).lower()}"
    raise SystemExit("Link Twitch non valido: " + s)


def vod_channel(vid):
    try:
        d = gql("query($id:ID!){video(id:$id){owner{login}}}", {"id": vid})
        return d["data"]["video"]["owner"]["login"]
    except Exception:
        return "streamer"


def main():
    cfg = load_cfg()
    os.makedirs(WORK, exist_ok=True)
    channels = {c["nome"].lower(): c for c in cfg.get("canali", [])}
    link = (os.environ.get("LINK") or "").strip()
    minutes = float(os.environ.get("MINUTI") or cfg.get("minuti", 10))
    start = float(os.environ.get("DA") or 0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    status = {"controllato": int(time.time()), "canali": []}
    jobs = []
    test = os.environ.get("TEST_FILE")
    if test:
        jobs.append(("test", "teststreamer", cfg.get("canali", [{}])[0].get("facecam", "br"), test))
    elif link:
        kind, ident, url = parse_link(link)
        ch = vod_channel(ident) if kind == "vod" else ident
        jobs.append((url, ch, channels.get(ch, {}).get("facecam", cfg.get("facecam_default", "br")), None))
    else:
        log("Scout: controllo", len(channels), "streamer")
        for ch, c in channels.items():
            live, game, title = live_info(ch)
            gta = live and (is_gta(game, title) or not cfg.get("solo_gta", True))
            status["canali"].append({"nome": ch, "live": live, "gioco": game, "gta": bool(gta)})
            log(f"  {ch}: " + ("LIVE " + (game or "?") if live else "offline") + (" -> clippo" if gta else ""))
            if gta:
                jobs.append((f"https://www.twitch.tv/{ch}", ch, c.get("facecam", cfg.get("facecam_default", "br")), None))
        if not jobs and cfg.get("scopri_top_gta", True):
            for login, viewers in top_gta_live(20):
                if login in channels or login in cfg.get("escludi", []):
                    continue
                log(f"Scout: nessuno della lista in live, prendo {login} ({viewers} spettatori su GTA V)")
                status["canali"].append({"nome": login, "live": True, "gioco": "Grand Theft Auto V", "gta": True, "scoperto": True})
                jobs.append((f"https://www.twitch.tv/{login}", login, cfg.get("facecam_default", "br"), None))
                if len(jobs) >= int(cfg.get("max_live_per_giro_scoperti", 1)):
                    break
        jobs = jobs[: int(cfg.get("max_live_per_giro", 2))]
    made = []
    for i, (url, ch, cam, local) in enumerate(jobs):
        src = local or os.path.join(WORK, f"rec_{i}.ts")
        if not local and not record(url, minutes, start, src):
            continue
        try:
            made += process(src, ch, cam, cfg, f"{ch}_{stamp}_{i}")
        except Exception as e:
            log("Errore nell'analisi di", ch, "->", e)
        if not local:
            os.remove(src)
    status["nuove_clip"] = len(made)
    if made or not test:
        publish(made, status)
    log("Fine:", len(made), "clip nuove")


if __name__ == "__main__":
    main()
