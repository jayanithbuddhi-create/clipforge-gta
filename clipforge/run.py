#!/usr/bin/env python3
"""ClipForge GTA v2 - 4 agenti gratuiti su GitHub Actions.

  Scout  - trova le live GTA V in inglese (lista tua + le piu' seguite) e le pubblica nella sezione "Live ora"
  Hunter - registra la live, trova la facecam da solo e misura l'hype secondo per secondo (urla + azione)
  Judge  - vota la viralita' 0-100, sceglie le clip (apertura sul picco, fine dopo il payoff)
  Editor - 9:16 con facecam sopra e gioco sotto, micro zoom sui picchi, sottotitoli animati parola
           per parola (Whisper), titolo piccolo in inglese, teaser iniziale, credito allo streamer

Uso:  python clipforge/run.py scout|record|analyze|edit|publish   (un passo per agente nel workflow)
      TEST_FILE=video.mp4 python clipforge/run.py all              (prova locale)
"""
import json, math, os, re, shutil, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "_work")
PAGES = os.path.join(ROOT, "_pages")
STATE = os.path.join(WORK, "state.json")
FONTS = os.path.join(ROOT, "fonts")
FONT_B = os.path.join(FONTS, "Inter-Bold.otf")
FONT_S = os.path.join(FONTS, "Inter-SemiBold.otf")
CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
OUT_W, OUT_H, SPLIT, FPS = 1080, 1920, 0.4, 30
CAM_PRESETS = {"tl": [0.01, 0.02, 0.24, 0.30], "tr": [0.75, 0.02, 0.24, 0.30],
               "bl": [0.01, 0.66, 0.24, 0.30], "br": [0.74, 0.66, 0.24, 0.30]}

# ---------------------------------------------------------------- stili (dalla ricerca sui clipper GTA)
STYLES = {
    "hype": {"nome": "Hype & rage", "colore": "&H0000D4FF", "w_audio": 1.6, "w_scene": 0.5, "durata": 26, "dopo": 5,
             "zoom": 0.14, "shake": 0,
             "hooks": {"both": ["{n} completely lost it", "{n} was NOT ready for this", "This is why {n} rage quits",
                                "{n}'s reaction says everything"],
                       "audio": ["{n} completely lost it", "{n} could not believe it", "{n} screamed so loud here"],
                       "scene": ["{n} did not see this coming", "Wait for {n}'s reaction"],
                       "weak": ["Wait for {n}'s reaction"]},
             "cta": ["Would you have stayed calm? 👇", "Rate this reaction 1-10 👇"], "emoji": "😭"},
    "funny": {"nome": "Funny RP", "colore": "&H0091FF4D", "w_audio": 1.5, "w_scene": 0.4, "durata": 30, "dopo": 5,
              "zoom": 0.10, "shake": 0,
              "hooks": {"both": ["This RP scene went so wrong", "{n} had no idea what was coming", "Nobody in the city expected this"],
                        "audio": ["{n} said this with a straight face", "The way {n} said this", "{n} cannot be serious"],
                        "scene": ["{n} really thought this would work", "This plan was doomed from the start"],
                        "weak": ["{n} really thought this would work"]},
              "cta": ["Tag someone who would do this 💀", "Who's the funniest in the city? 👇"], "emoji": "💀"},
    "action": {"nome": "Inseguimenti & azione", "colore": "&H005C3BFF", "w_audio": 1.1, "w_scene": 1.0, "durata": 25, "dopo": 4,
               "zoom": 0.16, "shake": 9,
               "hooks": {"both": ["{n}'s getaway went way too far", "The cops did not expect this", "Best escape in GTA RP?"],
                         "audio": ["{n} almost got caught here", "This chase got intense"],
                         "scene": ["Watch this getaway till the end", "This crash was not in the plan", "{n} pulled this off??"],
                         "weak": ["Watch this getaway till the end"]},
               "cta": ["Would you have escaped? 👇", "Cops or robbers? 👇"], "emoji": "🚔"},
    "drama": {"nome": "Drama RP", "colore": "&H00FF8CB4", "w_audio": 1.7, "w_scene": 0.3, "durata": 45, "dopo": 6,
              "zoom": 0.08, "shake": 0,
              "hooks": {"both": ["This argument got out of hand", "{n} finally snapped", "This RP drama was insane"],
                        "audio": ["{n} said what everyone was thinking", "This conversation got heated", "{n} did not hold back"],
                        "scene": ["This is how it all ended", "{n} made the wrong call"],
                        "weak": ["{n} made the wrong call"]},
              "cta": ["Who was right here? 👇", "Whose side are you on? 👇"], "emoji": "😳"},
}


def log(*a):
    print("[ClipForge]", *a, flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def load_cfg():
    with open(os.path.join(ROOT, "config.json")) as f:
        return json.load(f)


def save_state(st):
    os.makedirs(WORK, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"jobs": [], "clips": []}


# ================================================================ SCOUT
def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request("https://gql.twitch.tv/gql", data=body,
                                 headers={"Client-ID": CID, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


NODE = ("node{title viewersCount language createdAt previewImageURL(width:440,height:248) "
        "broadcaster{login displayName profileImageURL(width:70)} freeformTags{name}}")


def english_gta_lives(n=30):
    try:
        d = gql('query($n:Int){game(name:"Grand Theft Auto V"){streams(first:$n, options:{broadcasterLanguages:[EN]})'
                '{edges{%s}}}}' % NODE, {"n": n})
        edges = (((d.get("data") or {}).get("game") or {}).get("streams") or {}).get("edges") or []
    except Exception as e:
        log("Scout: lista live non disponibile ->", e)
        return []
    out = []
    for e in edges:
        nd = e.get("node") or {}
        b = nd.get("broadcaster") or {}
        if not b.get("login") or (nd.get("language") or "EN") != "EN":
            continue
        out.append({"login": b["login"].lower(), "nome": b.get("displayName") or b["login"], "titolo": nd.get("title") or "",
                    "spettatori": nd.get("viewersCount") or 0, "anteprima": nd.get("previewImageURL") or "",
                    "avatar": b.get("profileImageURL") or "", "tag": [t["name"] for t in nd.get("freeformTags") or []][:4],
                    "dal": nd.get("createdAt") or ""})
    out.sort(key=lambda l: -l["spettatori"])
    return out


def channel_live(login):
    try:
        d = gql('query($l:String!){user(login:$l){login displayName profileImageURL(width:70) stream{title viewersCount '
                'language createdAt previewImageURL(width:440,height:248) game{name}}}}', {"l": login})
        u = (d.get("data") or {}).get("user")
        if not u or not u.get("stream"):
            return None
        s = u["stream"]
        return {"login": login, "nome": u.get("displayName") or login, "titolo": s.get("title") or "",
                "spettatori": s.get("viewersCount") or 0, "anteprima": s.get("previewImageURL") or "",
                "avatar": u.get("profileImageURL") or "", "gioco": (s.get("game") or {}).get("name") or "",
                "lingua": s.get("language") or "", "dal": s.get("createdAt") or "", "tag": []}
    except Exception as e:
        log("Scout:", login, "non controllabile ->", e)
        return None


def is_gta(game, title):
    t = f"{game} {title}".lower()
    return "grand theft auto" in t or re.search(r"\bgta\b|\bgtarp\b|nopixel", t) is not None


def auto_style(title, tags):
    t = (title + " " + " ".join(tags)).lower()
    if re.search(r"chase|cop|police|heist|robber|race|getaway", t):
        return "action"
    if re.search(r"\brp\b|roleplay|nopixel|role play|prodigy|onx", t):
        return "funny"
    return "hype"


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
        return gql("query($id:ID!){video(id:$id){owner{login}}}", {"id": vid})["data"]["video"]["owner"]["login"]
    except Exception:
        return "streamer"


def cam_for(cfg, ch):
    for c in cfg.get("canali", []):
        if c["nome"].lower() == ch:
            return c.get("facecam", "auto")
    return cfg.get("facecam_default", "auto")


def recent_clipped(window=3600):
    try:
        clips = json.load(open(os.path.join(PAGES, "clips.json")))
    except Exception:
        return set()
    now = time.time()
    return {c["ch"] for c in clips if now - c.get("created", 0) < window}


def scout():
    cfg = load_cfg()
    fetch_pages()
    st = {"jobs": [], "clips": [], "inizio": int(time.time())}
    link = (os.environ.get("LINK") or "").strip()
    style_in = (os.environ.get("STILE") or "").strip()
    cam_in = (os.environ.get("FACECAM") or "").strip()
    minutes = float(os.environ.get("MINUTI") or cfg.get("minuti", 10))
    lives = english_gta_lives(30)
    log(f"Scout: {len(lives)} live GTA V in inglese adesso")
    mine = []
    for c in cfg.get("canali", []):
        info = channel_live(c["nome"].lower())
        if info and is_gta(info["gioco"], info["titolo"]):
            info["tuo"] = True
            mine.append(info)
            log(f"  {info['login']}: LIVE su GTA ({info['spettatori']} spettatori)")
        else:
            log(f"  {c['nome']}: " + ("live ma non su GTA" if info else "offline"))
    seen = {m["login"] for m in mine}
    excl = set(x.lower() for x in cfg.get("escludi", []))
    live_list = [l for l in mine + [l for l in lives if l["login"] not in seen] if l["login"] not in excl]
    for l in live_list:
        l["stile"] = auto_style(l["titolo"], l.get("tag", []))
    if link:
        kind, ident, url = parse_link(link)
        ch = vod_channel(ident) if kind == "vod" else ident
        info = next((l for l in live_list if l["login"] == ch), {})
        st["jobs"].append({"url": url, "ch": ch, "stile": style_in or info.get("stile") or cfg.get("stile_default", "hype"),
                           "facecam": cam_in or cam_for(cfg, ch), "minuti": minutes,
                           "da": float(os.environ.get("DA") or 0), "titolo": info.get("titolo", "")})
    elif os.environ.get("SOLO_SCOUT") != "1":
        recent = recent_clipped()
        pool = mine + ([l for l in live_list if not l.get("tuo")] if cfg.get("scopri_top_gta", True) else [])
        for l in pool:
            if len(st["jobs"]) >= int(cfg.get("max_live_per_giro", 1)):
                break
            if l["login"] in recent and not l.get("tuo"):
                continue
            st["jobs"].append({"url": f"https://www.twitch.tv/{l['login']}", "ch": l["login"], "stile": l["stile"],
                               "facecam": cam_for(cfg, l["login"]), "minuti": minutes, "da": 0, "titolo": l["titolo"]})
    for j in st["jobs"]:
        if j["stile"] not in STYLES:
            j["stile"] = "hype"
        log(f"Scout: clippo {j['ch']} in stile {STYLES[j['stile']]['nome']} ({j['minuti']:.0f} min)")
    st["live"] = live_list[:24]
    save_state(st)
    publish(st, final=False)


# ================================================================ HUNTER: registrazione
QUALITY = "1080p60,1080p,936p60,900p60,720p60,720p,best"


def record():
    st = load_state()
    for i, j in enumerate(st["jobs"]):
        out = os.path.join(WORK, f"rec_{i}.ts")
        cmd = ["streamlink", "--twitch-disable-ads", "--retry-open", "3", "--retry-streams", "5",
               "--stream-segment-threads", "3", "--hls-duration", str(int(j["minuti"] * 60)), "-f", "-o", out]
        if j.get("da"):
            cmd += ["--hls-start-offset", str(int(j["da"] * 60))]
        cmd += [j["url"], QUALITY]
        log(f"Hunter: registro {j['ch']} per {j['minuti']:.0f} min")
        try:
            subprocess.run(cmd, check=False, timeout=j["minuti"] * 60 + 600)
        except subprocess.TimeoutExpired:
            log("Hunter: registrazione oltre il tempo, uso quello che c'e'")
        j["file"] = out if os.path.exists(out) and os.path.getsize(out) > 2_000_000 else None
        if not j["file"]:
            log("Hunter: niente di utile da", j["ch"])
    save_state(st)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", path], capture_output=True, text=True).stdout
    j = json.loads(out)
    return int(j["streams"][0]["width"]), int(j["streams"][0]["height"]), float(j["format"]["duration"])


# ================================================================ HUNTER: analisi
def audio_db(path, n):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    out = np.full(n, -90.0)
    for s in range(n):
        seg = a[s * 8000:(s + 1) * 8000]
        if len(seg):
            out[s] = max(-90.0, 20 * math.log10(math.sqrt(float(np.mean(seg ** 2))) + 1e-9))
    return out


def motion(path, n, cam):
    W, H = 48, 27
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf", f"fps=1,scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, dtype=np.uint8).reshape(-1, H, W).astype(np.float32)
    mask = np.ones((H, W), bool)
    if cam:
        x, y, w, h = cam
        mask[int(y * H):int(math.ceil((y + h) * H)), int(x * W):int(math.ceil((x + w) * W))] = False
    mot, cx = np.zeros(n), np.full(n, 0.5)
    xs = (np.arange(W) + 0.5) / W
    for i in range(1, min(n, len(fr))):
        d = np.abs(fr[i] - fr[i - 1]) * mask
        mot[i] = d.sum() / max(1, mask.sum()) / 255
        big = np.where(d > 18, d, 0)
        if big.sum() > 0:
            cx[i] = float((big.sum(axis=0) * xs).sum() / big.sum())
    return mot, cx


def find_facecam(path, dur, sw, shh):
    """Cerca la webcam dello streamer: una faccia che resta nello stesso punto in quasi tutti i fotogrammi."""
    try:
        import cv2
    except Exception:
        return None
    xml = os.environ.get("HAAR_XML") or os.path.join(getattr(getattr(cv2, "data", None), "haarcascades", ""),
                                                      "haarcascade_frontalface_default.xml")
    casc = cv2.CascadeClassifier(xml) if os.path.exists(xml) else None
    if casc is None or casc.empty():
        log("Hunter: rilevatore di volti non disponibile")
        return None
    hits, N = [], 14
    h = int(round(960 * shh / sw / 2) * 2)
    for k in range(N):
        t = dur * (k + 0.5) / N
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.1f}", "-i", path, "-frames:v", "1",
                              "-vf", f"scale=960:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(raw) < 960 * h:
            continue
        img = np.frombuffer(raw[:960 * h], np.uint8).reshape(h, 960)
        for (x, y, w, hh) in casc.detectMultiScale(img, 1.1, 6, minSize=(36, 36)):
            hits.append((k, (x + w / 2) / 960, (y + hh / 2) / h, w / 960, hh / h))
    if not hits:
        return None
    best, best_n = None, 0
    for _, cx, cy, fw, fh in hits:
        near = [q for q in hits if abs(q[1] - cx) < 0.04 and abs(q[2] - cy) < 0.06]
        frames = len({q[0] for q in near})
        if frames > best_n:
            best_n, best = frames, near
    if best_n < max(4, int(N * 0.45)):
        return None
    cx = float(np.median([b[1] for b in best])); cy = float(np.median([b[2] for b in best]))
    fw = float(np.median([b[3] for b in best]))
    w = min(0.45, fw * 2.8)
    h = min(0.55, w * sw / shh / (OUT_W / (OUT_H * SPLIT)))   # stessa forma del riquadro facecam della clip
    x = min(max(0, cx - w / 2), 1 - w); y = min(max(0, cy - h * 0.45), 1 - h)
    log(f"Hunter: facecam trovata in {best_n}/{N} fotogrammi a ({cx:.2f},{cy:.2f})")
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def smooth(x, w=3):
    k = np.ones(w) / w
    return np.convolve(np.pad(x, (w // 2, w // 2), mode="edge"), k, mode="valid")[:len(x)]


def robust_z(x):
    md = np.median(x)
    mad = np.median(np.abs(x - md)) * 1.4826
    if mad < 1e-6:
        sd = x.std()
        return np.zeros_like(x) if sd < 1e-6 else np.maximum(0, (x - x.mean()) / sd)
    return np.maximum(0, (x - md) / mad)


def baseline_z(x, w=60):
    n, h = len(x), w // 2
    d = np.array([x[i] - np.median(x[max(0, i - h):min(n, i + h + 1)]) for i in range(n)])
    mad = np.median(np.abs(d)) * 1.4826 or 1
    return np.maximum(0, d / mad)


def candidates(score, max_n, length, after, dur):
    n = len(score)
    taken, picks = np.zeros(n, bool), []
    order = np.argsort(-score)
    min_s = max(0.8, 0.2 * score[order[0]])
    for i in order:
        if len(picks) >= max_n:
            break
        if taken[i] or (score[i] < min_s and picks):
            continue
        end = min(dur, i + after)                      # fine subito dopo il payoff
        start = max(0, end - length)
        end = min(dur, start + length)
        if end - start < min(8, dur):
            continue
        picks.append({"peak": int(i), "start": float(start), "end": float(end), "score": float(score[i])})
        taken[max(0, int(start) - 3):min(n, int(math.ceil(end)) + 3)] = True
    return picks


def micro_peaks(score, zA, zS, c, mx, k=4):
    """Picchi secondari dentro la clip: dove mettere i micro zoom (faccia se urla, gioco se azione)."""
    a, b = int(c["start"]), int(c["end"])
    out = []
    for i in sorted(range(a + 1, b - 1), key=lambda i: -score[i]):
        if score[i] < 0.42 * mx or score[i] < score[i - 1] or score[i] < score[i + 1]:
            continue
        if all(abs(i - o["t"]) >= 3 for o in out):
            out.append({"t": i, "forza": float(min(1.0, score[i] / mx)), "su": "faccia" if zA[i] >= zS[i] else "gioco"})
        if len(out) >= k:
            break
    return sorted(out, key=lambda o: o["t"])


def judge(c, zA, zS, score, mx):
    n = len(score)
    a, b = int(c["start"]), min(n, int(math.ceil(c["end"])))
    win = score[a:b]
    mean = float(win.mean()) if len(win) else 0
    near = [i for i in range(c["peak"] - 4, c["peak"] + 5) if 0 <= i < n]
    pa, ps = float(max(zA[near])), float(max(zS[near]))
    has_a, has_s = pa > 1.2, ps > 1.2
    peaks = int((win > 0.5 * mx).sum())
    v = 30 + 38 * c["score"] / mx + 12 * min(1, mean / (0.45 * mx)) + (12 if has_a and has_s else 7 if has_a else 4 if has_s else 0) \
        + min(6, peaks) + 4
    pct = lambda x: int(round(max(0, min(1, x)) * 100))
    c.update(viral=int(round(max(5, min(98, v)))), has_a=bool(has_a), has_s=bool(has_s),
             subs={"Hook": pct(0.35 + 0.65 * c["score"] / mx), "Azione": pct(ps / 4), "Reazione": pct(pa / 4),
                   "Ritmo": pct(0.25 * min(4, peaks) + 0.6 * mean / mx)})
    return c


def analyze():
    st, cfg = load_state(), load_cfg()
    st["analisi"] = []
    for j in st["jobs"]:
        if not j.get("file"):
            continue
        src = j["file"]
        sw, shh, dur = probe(src)
        style = STYLES.get(j["stile"], STYLES["hype"])
        cam = None
        if j["facecam"] == "auto":
            cam = find_facecam(src, dur, sw, shh)
            if not cam:
                log("Hunter: nessuna facecam fissa trovata, uso il gioco a tutto schermo")
        elif j["facecam"] in CAM_PRESETS:
            cam = CAM_PRESETS[j["facecam"]]
        elif isinstance(j["facecam"], list):
            cam = j["facecam"]
        n = int(math.ceil(dur)) + 1
        log(f"Hunter: analizzo {j['ch']} ({sw}x{shh}, {dur / 60:.1f} min)")
        try:
            zA = smooth(baseline_z(audio_db(src, n), 60), 3)
        except Exception as e:
            log("Hunter: audio non leggibile ->", e)
            zA = np.zeros(n)
        mot, cx = motion(src, n, cam)
        zS = smooth(robust_z(mot), 5)
        score = smooth(style["w_audio"] * zA + style["w_scene"] * zS + 0.35 * np.minimum(zA, zS), 3)
        score[:3] *= 0.3
        score[-3:] *= 0.3
        mx = max(1e-6, float(score.max()))
        keep = int(cfg.get("clip_per_live", 2))
        length = min(style["durata"], dur)
        cands = [judge(c, zA, zS, score, mx) for c in candidates(score, max(keep * 2 + 2, 6), length, style["dopo"], dur)]
        cands.sort(key=lambda c: -c["viral"])
        chosen = [c for k, c in enumerate(cands) if k < keep and (c["viral"] >= cfg.get("voto_minimo", 55) or k == 0)]
        log(f"Judge: {len(cands)} candidati, tengo {len(chosen)} -> " + ", ".join(str(c["viral"]) for c in chosen))
        for c in chosen:
            a, b = int(c["start"]), int(math.ceil(c["end"]))
            c["cx"] = float(np.clip(0.5 + (np.median(cx[a:b]) - 0.5) * 0.8, 0.2, 0.8)) if cfg.get("segui_azione", True) else 0.5
            c["zooms"] = micro_peaks(score, zA, zS, c, mx) if cfg.get("micro_zoom", True) else []
        step = max(1, n // 160)
        st["analisi"].append({"ch": j["ch"], "durata": round(dur), "passo": step, "stile": j["stile"],
                              "hype": [round(float(v), 2) for v in score[::step]],
                              "urla": [round(float(v), 2) for v in zA[::step]],
                              "azione": [round(float(v), 2) for v in zS[::step]],
                              "scelte": [{"start": round(c["start"]), "end": round(c["end"]), "viral": c["viral"],
                                          "zoom": [z["t"] for z in c["zooms"]]} for c in chosen],
                              "candidati": len(cands), "facecam": bool(cam), "quando": int(time.time())})
        j.update(sw=sw, shh=shh, dur=dur, cam=cam, chosen=chosen)
    save_state(st)


# ================================================================ EDITOR
def even(v):
    return max(2, int(v) // 2 * 2)


def zoom_filter(peaks, amp, w, h, shake=0):
    """Micro zoom: entra veloce (0,12 s) e torna piano (0,45 s) su ogni picco."""
    if not peaks:
        return f"scale={w}:{h}:flags=lanczos"
    T = f"(on/{FPS})"
    terms = [f"{amp * (0.6 + 0.4 * p['forza']):.3f}*if(lt({T},{p['tt']:.2f}),exp(-pow(({T}-{p['tt']:.2f})/0.12,2)),"
             f"exp(-pow(({T}-{p['tt']:.2f})/0.45,2)))" for p in peaks]
    z = "1+" + "+".join(terms)
    sx = f"+{shake}*sin(on*2.1)*(zoom-1)/{amp:.3f}" if shake else ""
    return f"zoompan=z='{z}':d=1:s={w}x{h}:fps={FPS}:x='iw/2-(iw/zoom/2){sx}':y='ih/2-(ih/zoom/2)'"


def build_base(src, j, c, style, out, teaser=True):
    """Primo passaggio: layout 9:16 + micro zoom + teaser. Senza testi (prima servono i tempi dei sottotitoli)."""
    sw, shh, cam = j["sw"], j["shh"], j["cam"]
    split = cam is not None and sw > shh
    top = int(OUT_H * SPLIT) if split else 0
    gh_out = OUT_H - top
    parts = [(c["peak"] - 0.4, 1.5), (c["start"], c["end"] - c["start"])] \
        if teaser and c["peak"] - c["start"] > 4 else [(c["start"], c["end"] - c["start"])]
    args, fc = ["ffmpeg", "-v", "error", "-y"], []
    for (ss, d) in parts:
        args += ["-ss", f"{ss:.2f}", "-t", f"{d:.2f}", "-i", src]
    for i, (ss, d) in enumerate(parts):
        local = [dict(z, tt=z["t"] - ss) for z in c.get("zooms", []) if 0.3 < z["t"] - ss < d - 0.3]
        if i == 0 and len(parts) == 2:
            local = [{"tt": 0.45, "forza": 1.0, "su": "gioco"}]
        face = [z for z in local if z["su"] == "faccia"]
        game = [z for z in local if z["su"] == "gioco"] if split else local
        if split:
            x, y, w, h = cam
            cw, chh, cx0, cy0 = even(w * sw), even(h * shh), even(x * sw), even(y * shh)
            gw = min(sw, even(shh * OUT_W / gh_out))
            gx = even(max(0, min(sw - gw, c["cx"] * sw - gw / 2)))
            fc.append(f"[{i}:v]fps={FPS},split=2[c{i}][g{i}];"
                      f"[c{i}]crop={cw}:{chh}:{cx0}:{cy0},scale={OUT_W}:{top}:force_original_aspect_ratio=increase:flags=lanczos,"
                      f"crop={OUT_W}:{top},setsar=1,{zoom_filter(face, style['zoom'] * 1.2, OUT_W, top)},setsar=1[cc{i}];"
                      f"[g{i}]crop={gw}:{shh}:{gx}:0,scale={OUT_W}:{gh_out}:flags=lanczos,setsar=1,"
                      f"{zoom_filter(game, style['zoom'], OUT_W, gh_out, style['shake'])},setsar=1[gg{i}];"
                      f"[cc{i}][gg{i}]vstack,format=yuv420p[v{i}]")
        else:
            gw2 = min(sw, even(shh * OUT_W / OUT_H))
            gx2 = even(max(0, min(sw - gw2, c["cx"] * sw - gw2 / 2)))
            fc.append(f"[{i}:v]fps={FPS},crop={gw2}:{shh}:{gx2}:0,scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,"
                      f"{zoom_filter(local, style['zoom'], OUT_W, OUT_H, style['shake'])},setsar=1,format=yuv420p[v{i}]")
        fc.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    if len(parts) == 2:
        fc.append("[v1]fade=t=in:st=0:d=0.2:color=white[v1f];[v0][a0][v1f][a1]concat=n=2:v=1:a=1[v][a]")
    else:
        fc.append("[v0]null[v];[a0]anull[a]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "15", "-c:a", "aac", "-b:a", "192k", out]
    sh(args)
    return split, top, (1.5 if len(parts) == 2 else 0), sum(p[1] for p in parts)


_WHISPER = None


def transcribe(path, model_name):
    global _WHISPER
    try:
        from faster_whisper import WhisperModel
    except Exception:
        log("Editor: Whisper non installato, niente sottotitoli")
        return []
    try:
        if _WHISPER is None:
            _WHISPER = WhisperModel(model_name, device="cpu", compute_type="int8")
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout          # audio gia' pronto: niente PyAV
        audio = np.frombuffer(raw, dtype=np.float32).copy()
        segs, _ = _WHISPER.transcribe(audio, word_timestamps=True, vad_filter=True, language="en", beam_size=3)
        words = []
        for s in segs:
            for w in s.words or []:
                t = w.word.strip()
                if t:
                    words.append({"t0": float(w.start), "t1": float(w.end), "w": t})
        return words
    except Exception as e:
        log("Editor: trascrizione fallita ->", e)
        return []


def ass_time(t):
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def make_ass(words, style, path, y):
    """Sottotitoli stile TikTok: 2-3 parole alla volta, parola attiva colorata con un piccolo 'pop'."""
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            "Style: Cap,Inter,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,6,3,5,60,60,0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= 3 or w["t0"] - cur[-1]["t1"] > 0.6 or len(" ".join(x["w"] for x in cur + [w])) > 22
                    or re.search(r"[.!?]$", cur[-1]["w"])):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    lines = []
    for g in groups:
        for k, w in enumerate(g):
            t1 = g[k + 1]["t0"] if k + 1 < len(g) else max(w["t1"], w["t0"] + 0.3)
            txt = []
            for m, x in enumerate(g):
                clean = re.sub(r"[{}\\]", "", x["w"]).upper()
                txt.append("{\\c" + style["colore"] + "&\\fscx114\\fscy114\\t(0,90,\\fscx100\\fscy100)}" + clean + "{\\c&H00FFFFFF&}"
                           if m == k else clean)
            lines.append(f"Dialogue: 0,{ass_time(w['t0'])},{ass_time(t1)},Cap,,0,0,0,,{{\\pos(540,{y})}}" + " ".join(txt))
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")
    return len(lines)


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


BAD = re.compile(r"\b(fuck|shit|bitch|nigg|fag|retard|cunt|whore)", re.I)


def quote_hook(words, peak_t):
    """Frase corta e forte detta vicino al picco = hook tra virgolette (formato molto usato nei clip RP)."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?]$", w["w"]):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    best, score = None, 0
    for s in sents:
        txt = " ".join(x["w"] for x in s).strip()
        n = len(txt.split())
        if not (2 <= n <= 8) or abs(s[0]["t0"] - peak_t) > 6 or BAD.search(txt):
            continue
        sc = (2 if "!" in txt else 1 if "?" in txt else 0.3) + \
             (1 if re.search(r"\b(no way|bro|what|why|oh my|run|dude|wait|stop)\b", txt.lower()) else 0)
        if sc > score:
            best, score = txt, sc
    return f'"{best.rstrip(".")}"' if best and score >= 1.3 else None


def style_hex(style):
    c = style["colore"][4:]          # &H00BBGGRR -> RRGGBB
    return c[4:6] + c[2:4] + c[0:2]


def finish(base, c, ch, style, split, top, teaser_len, total, words, out_mp4, out_jpg):
    hook_file, ch_file, ass_file = (os.path.join(WORK, f) for f in ("hook.txt", "ch.txt", "subs.ass"))
    with open(hook_file, "w") as f:
        f.write(wrap(c["hook"]))
    with open(ch_file, "w") as f:
        f.write(f"twitch.tv/{ch}")
    sub_y = (top + int((OUT_H - top) * 0.6)) if split else 1330
    n_sub = make_ass(words, style, ass_file, sub_y) if words else 0
    nl = wrap(c["hook"]).count("\n") + 1
    ty = (top if split else 250) - (nl * 56) // 2
    esc = lambda p: p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = []
    if split:
        vf.append(f"drawbox=x=0:y={top - 2}:w=iw:h=4:color=black:t=fill")
    if n_sub:
        vf.append(f"subtitles='{esc(ass_file)}':fontsdir='{esc(FONTS)}'")
    vf.append(f"drawtext=fontfile='{esc(FONT_B)}':textfile='{esc(hook_file)}':fontsize=42:fontcolor=0x111111:line_spacing=12:"
              f"text_align=C:box=1:boxcolor=white:boxborderw=24:x=(w-text_w)/2:y={ty}:alpha='min(1,t/0.25)'")
    vf.append(f"drawtext=fontfile='{esc(FONT_S)}':textfile='{esc(ch_file)}':fontsize=30:fontcolor=white:box=1:"
              f"boxcolor=black@0.55:boxborderw=14:x=(w-text_w)/2:y=h-250")
    fc = f"[0:v]{','.join(vf)}[vt];[vt][1:v]overlay=x='-w+W*t/{total:.2f}':y=H-10:shortest=1[v]"
    sh(["ffmpeg", "-v", "error", "-y", "-i", base, "-f", "lavfi", "-t", f"{total:.2f}", "-i",
        f"color=c=0x{style_hex(style)}:s={OUT_W}x10:r={FPS}", "-filter_complex", fc, "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", out_mp4])
    peak_at = teaser_len + (c["peak"] - c["start"])
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, peak_at):.2f}", "-i", out_mp4, "-frames:v", "1",
        "-vf", "scale=360:-2", "-q:v", "4", out_jpg])
    return n_sub


def caption(c, ch, style_key, title):
    """Dalla ricerca: 1 riga di hook con gioco e tipo di momento, 1 call to action, 5 hashtag pertinenti."""
    style = STYLES[style_key]
    slug = re.sub(r"\W", "", ch.lower())
    rp = bool(re.search(r"\brp\b|roleplay|nopixel", (title or "").lower())) or style_key in ("funny", "drama")
    moment = {"hype": "reaction", "funny": "RP moment", "action": "getaway", "drama": "RP drama"}[style_key]
    tags = [f"#{slug}", "#gtarp" if rp else "#gta5", "#gta", "#twitchclips", "#fyp"]
    cta = style["cta"][int(time.time()) % len(style["cta"])]
    return f"{c['hook'].strip(chr(34))} {style['emoji']} {ch} {moment} in GTA {'RP' if rp else 'V'}\n{cta}\n\n" + " ".join(dict.fromkeys(tags))


def edit():
    st, cfg = load_state(), load_cfg()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    made, used = [], set()
    for i, j in enumerate(st["jobs"]):
        if not j.get("chosen"):
            continue
        style = STYLES.get(j["stile"], STYLES["hype"])
        bank = style["hooks"]
        for k, c in enumerate(j["chosen"]):
            cid = f"{j['ch']}_{stamp}_{i}{k}"
            base, mp4, jpg = (os.path.join(WORK, cid + e) for e in ("_base.mp4", ".mp4", ".jpg"))
            log(f"Editor: clip {k + 1}/{len(j['chosen'])} di {j['ch']} (voto {c['viral']}, {len(c.get('zooms', []))} micro zoom)")
            try:
                split, top, teaser_len, total = build_base(j["file"], j, c, style, base, cfg.get("teaser", True))
                words = transcribe(base, cfg.get("whisper_model", "base.en")) if cfg.get("sottotitoli", True) else []
                kind = "both" if c["has_a"] and c["has_s"] else "audio" if c["has_a"] else "scene" if c["has_s"] else "weak"
                hook = quote_hook(words, teaser_len + c["peak"] - c["start"]) if j["stile"] != "action" else None
                opts = [h.format(n=j["ch"]) for h in bank[kind] + [x for kk in ("both", "audio", "scene") for x in bank[kk]]]
                off = int(time.time()) // 60
                pick = next((opts[(off + q) % len(opts)] for q in range(len(opts)) if opts[(off + q) % len(opts)] not in used), opts[0])
                c["hook"] = hook if hook and hook not in used else pick
                used.add(c["hook"])
                if words:                      # dialogo fitto = clip RP piu' guardabile: piccolo bonus al Judge
                    c["viral"] = int(min(98, c["viral"] + min(5, len(words) / max(1, total) * 2)))
                n_sub = finish(base, c, j["ch"], style, split, top, teaser_len, total, words, mp4, jpg)
                os.remove(base)
            except Exception as e:
                log("Editor: montaggio fallito ->", e)
                continue
            made.append({"id": cid, "file": f"clips/{cid}.mp4", "poster": f"clips/{cid}.jpg", "ch": j["ch"], "stile": j["stile"],
                         "hook": c["hook"], "caption": caption(c, j["ch"], j["stile"], j.get("titolo", "")),
                         "viral": c["viral"], "subs": c["subs"], "zoom": len(c.get("zooms", [])), "sottotitoli": n_sub > 0,
                         "facecam": bool(j.get("cam")), "durata": round(total, 1), "created": int(time.time()),
                         "at": round(c["start"]), "mb": round(os.path.getsize(mp4) / 1048576, 1)})
            log(f"Editor: fatto '{c['hook']}' · {n_sub} sottotitoli · {made[-1]['mb']} MB")
    st["clips"] = made
    save_state(st)


# ================================================================ PUBBLICA (galleria su gh-pages)
def fetch_pages():
    if os.path.isfile(os.path.join(PAGES, "index.html")):
        return
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    shutil.rmtree(PAGES, ignore_errors=True)
    if repo and token:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "gh-pages",
                            f"https://x-access-token:{token}@github.com/{repo}.git", PAGES])
        if r.returncode == 0:
            return
    os.makedirs(PAGES, exist_ok=True)


def publish(st=None, final=True):
    st = st or load_state()
    cfg = load_cfg()
    fetch_pages()
    os.makedirs(os.path.join(PAGES, "clips"), exist_ok=True)
    db_path = os.path.join(PAGES, "clips.json")
    try:
        clips = json.load(open(db_path))
    except Exception:
        clips = []
    new = st.get("clips", []) if final else []
    for c in new:
        shutil.copy(os.path.join(WORK, c["id"] + ".mp4"), os.path.join(PAGES, c["file"]))
        shutil.copy(os.path.join(WORK, c["id"] + ".jpg"), os.path.join(PAGES, c["poster"]))
    clips = new + clips
    keep = int(cfg.get("tieni_clip", 40))
    for old in clips[keep:]:
        for f in (old["file"], old["poster"]):
            p = os.path.join(PAGES, f)
            if os.path.exists(p):
                os.remove(p)
    clips = clips[:keep]
    json.dump(clips, open(db_path, "w"), ensure_ascii=False, indent=1)
    if "live" in st:
        json.dump({"aggiornato": int(time.time()), "live": st["live"]}, open(os.path.join(PAGES, "live.json"), "w"),
                  ensure_ascii=False, indent=1)
    try:
        status = json.load(open(os.path.join(PAGES, "status.json")))
    except Exception:
        status = {}
    status.update({"controllato": int(time.time()), "in_corso": not final,
                   "lavori": [{"ch": j["ch"], "stile": j["stile"], "minuti": j["minuti"]} for j in st.get("jobs", [])]})
    if final:
        status["nuove_clip"] = len(new)
        if st.get("analisi"):
            status["analisi"] = st["analisi"][-1]
    json.dump(status, open(os.path.join(PAGES, "status.json"), "w"), ensure_ascii=False, indent=1)
    shutil.copy(os.path.join(ROOT, "site", "index.html"), os.path.join(PAGES, "index.html"))
    open(os.path.join(PAGES, ".nojekyll"), "w").close()
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        log("Prova locale: galleria in", PAGES)
        return
    shutil.rmtree(os.path.join(PAGES, ".git"), ignore_errors=True)
    g = lambda *a: sh(["git", "-C", PAGES, *a])
    g("init", "-q", "-b", "gh-pages")
    g("config", "user.name", "ClipForge GTA")
    g("config", "user.email", "clipforge@users.noreply.github.com")
    g("add", "-A")
    g("commit", "-q", "-m", f"ClipForge: {len(new)} nuove clip" if final else "ClipForge: Scout")
    g("push", "-q", "-f", f"https://x-access-token:{token}@github.com/{repo}.git", "gh-pages")
    log("Pubblica: galleria aggiornata" + (f" con {len(new)} nuove clip" if final else " (live e stato)"))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.makedirs(WORK, exist_ok=True)
    if stage == "scout":
        scout()
    elif stage == "record":
        record()
    elif stage == "analyze":
        analyze()
    elif stage == "edit":
        edit()
    elif stage == "publish":
        publish(final=True)
    elif stage == "all":
        if os.environ.get("TEST_FILE"):
            save_state({"jobs": [{"url": "", "ch": "teststreamer", "stile": os.environ.get("STILE", "hype"),
                                  "facecam": os.environ.get("FACECAM", "auto"), "minuti": 0, "da": 0, "titolo": "NoPixel RP",
                                  "file": os.environ["TEST_FILE"]}], "clips": [], "live": []})
        else:
            scout()
            record()
        analyze()
        edit()
        publish(final=True)
