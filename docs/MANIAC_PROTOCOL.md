# Protocollo `maniac://` — invio download dal browser

Maniac registra il protocollo custom `maniac://` su Windows/macOS al primo
avvio (e durante l'installazione tramite NSIS). Una volta attivo, qualsiasi
URL nel formato `maniac://download?url=<URL_VIDEO>` aperto dal browser viene
intercettato dall'app: se Maniac è chiuso si avvia, altrimenti riceve l'URL
nella sessione già aperta e mostra il banner "Link rilevato dagli appunti"
con i pulsanti **Scarica** / **Wizard** / **Ignora**.

## Bookmarklet (consigliato)

1. Apri il browser e crea un nuovo segnalibro nella barra dei preferiti.
2. Come **URL** del segnalibro incolla questa riga:

   ```
   javascript:(function(){location.href='maniac://download?url='+encodeURIComponent(location.href)})();
   ```

3. Dai un nome al segnalibro (es. **Scarica con Maniac**).
4. Quando ti trovi su una pagina che vuoi scaricare (YouTube, Vimeo, una
   pagina del sito StreamingCommunity, ecc.) clicca il segnalibro: Maniac
   apre il banner di download con l'URL già pre-compilato.

## Variante per link specifici (anziché la pagina corrente)

Se vuoi scaricare un link puntato dal mouse (es. da un menu "Salva link
con nome"), usa questa variante che richiede di **selezionare il link** e
poi cliccare il bookmarklet:

```
javascript:(function(){var s=window.getSelection().toString().trim();if(!s){alert('Seleziona un link prima');return;}location.href='maniac://download?url='+encodeURIComponent(s)})();
```

## Estensione browser (opzionale, futuro)

Per integrazione nativa nel menu contestuale del browser ("Scarica con
Maniac…" sui link) servirà una estensione Chrome/Firefox dedicata che
costruisce la stessa URL `maniac://download?url=…` e la apre via
`chrome.tabs.update`. Non incluso nel pacchetto di base.

## Troubleshooting

- **Il browser dice "Apri con Maniac?" e poi non succede nulla** → autorizza
  il prompt una volta sola; Windows ricorderà la scelta.
- **Funziona ma non si vede il banner** → il banner appare in alto a
  destra della finestra Maniac per ~30 secondi; se Maniac è in tray
  cliccalo per portarlo in primo piano.
- **`maniac://` non viene riconosciuto** → reinstalla Maniac dal pacchetto
  NSIS (la chiave di registro per il protocollo viene scritta solo
  dall'installer). In alternativa, lancia Maniac come Admin almeno una
  volta: `app.setAsDefaultProtocolClient('maniac')` registra la chiave
  HKCU sul primo avvio.
