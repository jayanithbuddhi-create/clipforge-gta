# ClipForge GTA

4 agenti gratuiti su GitHub Actions che trasformano le live Twitch di GTA in clip verticali 9:16.

- **Scout**: ogni 20 minuti controlla gli streamer in `config.json` e vede chi è in live su GTA.
- **Hunter**: registra 10 minuti di live (senza pubblicità) e trova i picchi di urla e azione.
- **Judge**: dà un voto di viralità 0–100 e tiene solo le clip migliori.
- **Editor**: monta le clip 1080×1920 con facecam sopra e gioco sotto, titolo piccolo in inglese, teaser iniziale e credito allo streamer.

Le clip compaiono nella galleria GitHub Pages del progetto, da aprire sull'iPhone.

**Clippare subito un link:** Actions → ClipForge GTA → Run workflow → incolla il link Twitch.
**Cambiare streamer o facecam:** modifica `config.json` (`facecam`: `tl`, `tr`, `bl`, `br` oppure `none`).
