# Stash CommunityScrapers (popolata)

Sincronizzata da [stashapp/CommunityScrapers](https://github.com/stashapp/CommunityScrapers).

Cartella popolata automaticamente con 802 scraper YAML (~5 MB) per oltre 700 siti.

## Aggiornamento

Per ricevere gli aggiornamenti dalla community, rilancia:

```bash
cd /tmp && rm -rf CommunityScrapers
git clone --depth 1 https://github.com/stashapp/CommunityScrapers.git
cp -r /tmp/CommunityScrapers/scrapers/* "<repo>/models/scrapers/"
rm -rf /tmp/CommunityScrapers
```

## Engine in uso

`python/scraper_engine.py` carica tutti i `.yml` ricorsivamente e supporta:
- `sceneByName` con `action: scrapeXPath` — usato dall'auto-tag library
- `sceneByURL` con `action: scrapeXPath` — usato dal context-menu file/URL
- `xPathScrapers` (lxml) e `jsonScrapers` (limitato)

Voto consenso ≥ 3 fonti = match certo (`python/library_autotag.py`).
