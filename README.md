# ClipForge GTA

4 agenti gratuiti su GitHub Actions che trasformano GTA su Twitch in clip verticali 9:16.

- **Scout**: ogni 20 minuti fa la classifica dei **top streamer GTA in live** (per spettatori) e prende le **clip GTA più viste delle ultime 24 ore**.
- **Hunter**: scarica le clip virali e registra la live più seguita **leggendo anche la chat** (picchi di messaggi, KEKW, LMAO, "CLIP IT") e le clip create dagli spettatori durante la registrazione.
- **Judge**: voto **assoluto** 0–100. Ascolta i candidati con Whisper, scarta silenzi, menu e caricamenti, taglia la clip dove inizia la situazione e la chiude dopo il payoff. **Se in una live non succede niente, non pubblica nulla.**
- **Editor**: 1080×1920 con facecam sopra e gioco sotto, hook (titolo della clip virale, frase detta o hook dalla chat), micro zoom, sottotitoli animati, credito allo streamer.

Ogni clip in galleria mostra **perché** è stata scelta (es. "clippata dagli spettatori · chat esplosa").

**Clippare subito un link:** Actions → ClipForge GTA → Run workflow → incolla un link Twitch (live, VOD **o clip**).
**Impostazioni utili in `config.json`:** `voto_minimo` (soglia del Judge, 60), `clip_virali_per_giro` (2), `min_views_clip_virale` (1500), `chat` (true), `canali`, `escludi`, `facecam` (`tl`, `tr`, `bl`, `br`, `none`).
