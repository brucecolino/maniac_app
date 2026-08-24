# Changelog

Tutte le versioni pubblicate di Maniac. L'app controlla questa lista dalla
scheda **Info → Controlla aggiornamenti**.

## 1.1.3 — 2026-08-24

### Wizard download: si incolla e basta
- Via la griglia dei siti da scegliere. I link arrivano dagli appunti o
  incollati a mano, quindi al suo posto ci sono tre pulsanti diretti:
  **Incolla dagli appunti**, **Ricerca approfondita**, **Cerca per titolo**.

### Ricerca approfondita: i video che prima non si prendevano
- Per le pagine dove il video non è nel sorgente ma lo costruisce il lettore,
  l'app apre la pagina in una finestra nascosta e guarda cosa chiede alla
  rete. Tre problemi risolti, tutti misurati su una pagina reale:
- Il manifesto spesso **non ha estensione** (`/playlist/<id>?token=…`):
  cercare `.m3u8` non trovava nulla. Ora contano anche il percorso e il tipo
  di contenuto dichiarato dalla risposta.
- Certi siti, appena la pagina è pronta, la **sostituiscono con quella di
  login** e così spengono il lettore prima che chieda il video. Bloccando
  quella singola richiesta il flusso arriva in **1 secondo** invece di non
  arrivare mai.
- Il traffico è pieno di frammenti, anteprime e sottotitoli: restano fuori
  dall'elenco. Il flusso completo — tutte le qualità e tutte le lingue — va
  in testa, contrassegnato.
- Verificato da capo a fondo: il flusso trovato si scarica, con qualità fino
  a 1080p e traccia audio italiana e inglese.

### Correzioni
- Il controllo aggiornamenti non dichiara più «sei aggiornato» quando in
  realtà non è riuscito a leggere la versione pubblicata: ora dice che il
  controllo è fallito.
- Su finestre basse l'elenco dei flussi trovati veniva schiacciato a una
  fetta di riga illeggibile. Ora il dialogo scorre.
- Una scheda del wizard mostrava frammenti di codice al posto dell'icona.

## 1.1.2 — 2026-08-23

### Riconoscimento più preciso
- **Quando il riconoscimento era incerto usciva quasi sempre lo stesso nome.**
  Due cause, entrambe misurate sull'archivio reale.
- Alcune impronte salvate somigliano a moltissimi volti diversi — nascono da
  ritagli sfocati o parziali e vincono qualsiasi confronto, a prescindere da
  chi si sta cercando. L'app ora le riconosce da sola e smette di proporle:
  su un archivio di 26 nomi ne ha escluse 9, lasciando i 17 affidabili.
- I performer con pochissime foto nel database entravano fra le proposte con
  una sola conferma. Con due o tre foto il punteggio è il migliore di pochi
  tentativi, quindi capita facilmente per caso: misurato, chi ha 2 foto segna
  in media 0.31 contro volti estranei, chi ne ha 49 si ferma a 0.13. Ora
  servono due conferme indipendenti, oppure una somiglianza netta.

## 1.1.1 — 2026-08-23

### Correzioni
- **Il riconoscimento volti non funzionava nell'app installata**, mentre in
  sviluppo andava: l'installer escludeva tutte le cartelle chiamate `test`,
  ma in TensorFlow una di queste fa parte dell'API vera e propria
  (`tensorflow/_api/v2/__internal__/test`). Senza quella cartella l'intero
  motore AI non si avviava e l'analisi terminava con un errore di import.
  L'installer ora include il venv completo.

## 1.1.0 — 2026-08-23

### Riconoscimento persone
- **Attori, musicisti, sportivi e volti pubblici** ora vengono riconosciuti.
  Prima era impossibile: l'unica fonte attiva era StashDB, che copre solo
  contenuti adult, e i provider generalisti erano disattivati nel codice.
  Si affiancano Wikidata e Wikipedia (gratuiti, nessuna chiave) e TMDB, se
  inserisci la tua chiave in Impostazioni.
- Corretto l'errore che rendeva inutilizzabili Wikidata e Wikipedia: le loro
  API rifiutano le richieste prive di User-Agent, quindi rispondevano sempre
  con un errore.
- Le foto dei database vengono ora analizzate con lo stesso rilevatore di
  volti usato sui fotogrammi del video. Confrontare ritagli prodotti da
  rilevatori diversi falsava il risultato: una foto identica al volto cercato
  poteva segnare 0.31 di somiglianza.
- Nelle foto di gruppo viene scelto il volto principale e non il primo
  rilevato: negli scatti dal vivo veniva confrontato il pubblico sullo sfondo.
- Migliora anche il riconoscimento dei performer StashDB: sugli stessi file le
  foto concordi passano da 4 su 10 a 7 su 10, e da 3 su 10 a 10 su 10.

### Aggiornamenti automatici
- **Controlla aggiornamenti funziona davvero.** Prima rispondeva sempre "sei
  aggiornato" senza controllare nulla.
- Nuova finestra con numero di versione, note della release, dimensione del
  download, barra di avanzamento e velocità.
- Il download parte solo se lo chiedi, e l'installazione avviene quando
  decidi tu: nessun riavvio a sorpresa mentre stai guardando qualcosa.

### Interfaccia
- La scheda Info mostra la firma **Apice** con collegamento ad apicesite.com.
- Il numero di versione è letto dall'app, non più scritto a mano: non può
  più mostrare una versione diversa da quella installata.

---

## 1.0.1 e precedenti

Sviluppo iniziale: player multi-finestra, playlist, equalizzatore e
regolazioni audio per player, webcam e IP cam, registrazione, sottotitoli
automatici con traduzione, downloader (YouTube, link diretti, torrent,
clipboard), organizer, riconoscimento volti su StashDB, oggetti, animali e
luoghi.
