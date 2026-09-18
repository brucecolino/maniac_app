# Changelog

Tutte le versioni pubblicate di Maniac. L'app controlla questa lista dalla
scheda **Info → Controlla aggiornamenti**.

## 1.1.6 — 2026-09-18

### Organizer: riconosce anche quello che vede
- Nuova opzione **Tipologia dalle immagini**. Guarda otto fotogrammi per video e
  impara le *tue* tipologie anche dall'immagine, così finiscono in cartella pure
  i file col nome che non dice niente (`68c4eb094c99….mp4`). Risponde solo
  quando è abbastanza sicura: sotto la soglia tace e il video resta dov'è. Il
  modello (335 MB) si scarica al primo uso e i risultati restano in cache, quindi
  la prima analisi è lenta (circa mezzo secondo a video) e le successive no.
  Quando nome e immagini dicono la stessa cosa, la proposta sale a "probabile".
- **Doppioni**. Trova lo stesso video due volte in libreria — copia identica,
  ricodifica (impronta visiva) o stessa scena su StashDB — tiene quello già
  smistato o il più grande e segnala gli altri, con la spunta per mandarli tutti
  in una cartella a parte.
- **Sistemazione automatica**. Ogni tanto ricontrolla le cartelle da smistare e
  mette a posto da solo quello che riconosce con certezza. Spento di default, non
  tocca mai i doppioni e lascia sempre lo snapshot per annullare.

### Correzioni
- Il controllo aggiornamenti confronta i numeri di versione uno per uno: non
  propone più di "aggiornare" alla 1.1.3 quando hai già la 1.1.4.
- Nell'organizer classico la barra dice quanti file sono in tutto, non più solo
  quanti ne ha letti finora.

## 1.1.5 — 2026-09-18

### Organizer: si decide meglio cosa fare di ogni video
- Ogni riga dell'anteprima mostra un **fotogramma del video**, così i file senza
  indizi si riconoscono a occhio. L'interruttore **Anteprime** le accende e le
  spegne.
- Le righe **senza destinazione ora si selezionano**, e con **Manda i selezionati
  in…** scegli la cartella per tutto il gruppo in un colpo solo.
- **Tipologie gestibili a mano**: puoi aggiungere una cartella che esiste già
  oppure crearne una nuova, che nasce solo quando confermi gli spostamenti.
- Due nuove spunte: **tagga anche i file che restano dove sono** e **aggiungi a
  Maniac i performer che non ha ancora**, con la foto presa da StashDB.

## 1.1.4 — 2026-09-17

### Organizer: performer e tipologia, in automatico
- Nuovo wizard **Organizer → Organizza per performer e tipologia**. Legge la
  libreria, riconosce chi c'è nel video e di che tipo è, e propone dove
  spostarlo. Niente si muove finché non confermi l'anteprima, e lo spostamento
  si annulla con un clic.
- Riconoscimento in cinque passaggi, dal segnale più solido al più debole:
  impronta del file e **impronta visiva** su StashDB (regge le ricodifiche),
  codice scena nel nome (GIO2408, SZ2380…), titolo accettato solo se la durata
  coincide, nomi scritti nel file confrontati con le tue cartelle performer e
  con StashDB.
- La tipologia la impara dalle **tue** cartelle già smistate, e calibra le
  soglie sulla libreria stessa: "probabile" vuol dire davvero nove volte su
  dieci.
- Struttura finale a scelta: il tuo schema attuale, Tipologia › Performer,
  Performer › Tipologia, oppure solo tag senza spostare niente. Il ruolo di
  ogni cartella si può correggere prima di partire.
- Performer e tipologia diventano tag nel player, e seguono i file quando
  vengono spostati o riportati indietro.
- Impronte e risposte di StashDB restano in cache: la seconda analisi della
  stessa libreria è questione di secondi.
- Misurato su una libreria di 5.900 video: sui 2.256 file da smistare,
  performer riconosciuto per 1.398 e tipologia per 2.154.

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
