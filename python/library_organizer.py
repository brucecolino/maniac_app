#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""library_organizer.py — Organizer libreria: performer e tipologia.

Non conosce i nomi delle cartelle dell'utente. La struttura viene rilevata
(`detect`) e confermata nel wizard; ogni cartella di primo livello ha un ruolo:

  category             tipologia: i file stanno direttamente dentro
  category_performers  tipologia con sottocartelle per performer
  performers           solo sottocartelle per performer
  unsorted             da smistare (anche "Nuova cartella" ovunque si trovi)
  ignore               mai letta, mai inviata a StashDB

Riconoscimento del performer, dal segnale più solido al più debole:
  1. impronta OSHASH su StashDB: copia identica di un file già catalogato;
  2. codice scena nel nome (GIO2408, SZ2380…) con durata coerente;
  3. impronta visiva PHASH su StashDB: regge ricodifiche e cambi bitrate;
  4. titolo su StashDB, accettato solo se la durata coincide;
  5. nome nel file: cartelle performer dell'utente e performer già visti in
     libreria, poi StashDB (nome o alias identico).

Tipologia: classificatore bayesiano addestrato sui file già smistati
dall'utente (parole del nome + tag, studio e cast StashDB). Le soglie di
fiducia sono calibrate in validazione incrociata sulla libreria stessa, così
"probabile" significa davvero ≥ 90% di precisione su quei dati. Un file dentro
una cartella tipologia (es. GANGBANG\\Nuova cartella) eredita la tipologia.

CLI:
  python library_organizer.py detect  --root <dir> [--roles <json>]
  python library_organizer.py analyze --config <cfg.json> --cache <cache.sqlite> [--ffmpeg <exe>]

`analyze` emette JSONL: phase / progress / warn e un evento finale
{"type":"done","ok":true,"summary":{…},"items":[…],"targets":{…}}.
"""
import os, sys, re, json, math, time, sqlite3, argparse, unicodedata, difflib, random, traceback
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import videohash

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.flv', '.webm', '.ts', '.m2ts',
              '.mpg', '.mpeg', '.3gp', '.ogv', '.vob', '.divx', '.rmvb', '.asf'}

ROLE_CATEGORY = 'category'
ROLE_CATEGORY_PERFORMERS = 'category_performers'
ROLE_PERFORMERS = 'performers'
ROLE_UNSORTED = 'unsorted'
ROLE_IGNORE = 'ignore'
ROLES = (ROLE_CATEGORY, ROLE_CATEGORY_PERFORMERS, ROLE_PERFORMERS, ROLE_UNSORTED, ROLE_IGNORE)

LAYOUTS = ('current', 'category_performer', 'performer_category', 'tags_only')

DEFAULT_UNSORTED_NAMES = ['nuova cartella', 'new folder', 'senza titolo', 'untitled', 'da ordinare',
                          'da smistare', 'unsorted', 'to sort', 'inbox', 'download', 'downloads',
                          'new', 'nuovi']

PERFORMER_CONTAINER_RX = re.compile(
    r'\b(porn ?stars?|pornostars?|performers?|attrici|attori|actress(es)?|actors?|models?|modelle|stars)\b', re.I)
MEDIA_DIR_NAMES = {'video', 'videos', 'clip', 'clips', 'immagini', 'images', 'image', 'pics', 'pic',
                   'photos', 'photo', 'foto', 'hd', 'sd', '4k', 'full'}
IGNORE_TOP_NAMES = {'pics', 'pic', 'foto', 'photos', 'photo', 'immagini', 'images', 'screenshots',
                    'thumbs', 'thumbnails', 'subs', 'sottotitoli'}

CONF_RANK = {'certain': 3, 'probable': 2, 'uncertain': 1, 'none': 0}
MALE_GENDERS = {'MALE', 'TRANSGENDER_MALE'}

DAY = 86400


# Il flusso JSONL e' sempre UTF-8: l'app passa gia' PYTHONIOENCODING, ma chi lancia
# il worker a mano su Windows si ritroverebbe cp1252 e accenti rotti.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def _emit(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _warn(text):
    _emit({'type': 'warn', 'error': text})


class Progress:
    def __init__(self, phase, total, text):
        self.phase, self.total, self.t0, self.last = phase, total, time.time(), 0.0
        _emit({'type': 'phase', 'phase': phase, 'text': text, 'total': total})

    def tick(self, current, file=None, found=None, force=False):
        now = time.time()
        if not force and now - self.last < 0.3 and current < self.total:
            return
        self.last = now
        eta = None
        if current and self.total and current < self.total:
            eta = round((now - self.t0) / current * (self.total - current))
        _emit({'type': 'progress', 'phase': self.phase, 'current': current, 'total': self.total,
               'file': file, 'found': found, 'eta': eta})


# ─────────────────────────────────────────────────────────────────────
# Normalizzazione testi
# ─────────────────────────────────────────────────────────────────────
def _strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c))


def _squash(s):
    return re.sub(r'[^a-z]', '', _strip_accents(s).lower())


def _norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', _strip_accents(s).lower())).strip()


def _stem(tok):
    if len(tok) >= 4 and tok.endswith('s') and not tok.endswith('ss'):
        return tok[:-1]
    return tok


_UNSORTED_SUFFIX = re.compile(r'\s*\(?\d+\)?$')


def _is_unsorted_name(name, names):
    n = _norm(_UNSORTED_SUFFIX.sub('', name or ''))
    return bool(n) and n in names


# ─────────────────────────────────────────────────────────────────────
# Struttura libreria
# ─────────────────────────────────────────────────────────────────────
def _walk(root):
    tree = {}
    stack = ['']
    while stack:
        rel = stack.pop()
        full = os.path.join(root, rel) if rel else root
        files, dirs = [], []
        try:
            with os.scandir(full) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if not e.name.startswith('.') and e.name.lower() not in (
                                    '$recycle.bin', 'system volume information'):
                                dirs.append(e.name)
                        elif e.is_file() and os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                            files.append(e.name)
                    except OSError:
                        pass
        except OSError:
            pass
        tree[rel] = {'files': sorted(files), 'dirs': sorted(dirs)}
        for d in dirs:
            stack.append(os.path.join(rel, d) if rel else d)
    counts = {}
    for rel in sorted(tree, key=lambda r: -r.count(os.sep) if r else 1):
        node = tree[rel]
        counts[rel] = len(node['files']) + sum(
            counts.get(os.path.join(rel, d) if rel else d, 0) for d in node['dirs'])
    return tree, counts


def guess_top_role(name, tree, counts, unsorted_names):
    if _is_unsorted_name(name, unsorted_names):
        return ROLE_UNSORTED
    if counts.get(name, 0) == 0 or _norm(name) in IGNORE_TOP_NAMES:
        return ROLE_IGNORE
    node = tree[name]
    kids = [d for d in node['dirs'] if counts.get(os.path.join(name, d), 0) > 0]
    named = [d for d in kids if not _is_unsorted_name(d, unsorted_names) and _norm(d) not in MEDIA_DIR_NAMES]
    direct = len(node['files'])
    if direct == 0 and kids and not named:
        return ROLE_CATEGORY
    if len(named) >= 2 and direct <= max(2, int(0.1 * counts[name])):
        return ROLE_PERFORMERS if PERFORMER_CONTAINER_RX.search(name) else ROLE_CATEGORY_PERFORMERS
    return ROLE_CATEGORY


def _context(parts, role_of, unsorted_names):
    if not parts:
        return {'unsorted': True, 'category': None, 'perf_folder': None}
    top = parts[0]
    role = role_of.get(top, ROLE_CATEGORY)
    if role == ROLE_IGNORE:
        return None
    unsorted = role == ROLE_UNSORTED or any(_is_unsorted_name(p, unsorted_names) for p in parts[1:])
    category = top if role in (ROLE_CATEGORY, ROLE_CATEGORY_PERFORMERS) else None
    perf_folder = None
    if role in (ROLE_PERFORMERS, ROLE_CATEGORY_PERFORMERS) and len(parts) >= 2 \
            and not _is_unsorted_name(parts[1], unsorted_names):
        perf_folder = os.path.join(parts[0], parts[1])
    return {'unsorted': unsorted, 'category': category, 'perf_folder': perf_folder}


def build_library(root, roles=None, unsorted_names=None, extra_categories=None):
    unsorted_names = {_norm(n) for n in (unsorted_names or DEFAULT_UNSORTED_NAMES) if _norm(n)}
    tree, counts = _walk(root)
    tops = tree.get('', {}).get('dirs', [])
    role_of = {}
    for d in tops:
        r = (roles or {}).get(d)
        role_of[d] = r if r in ROLES else guess_top_role(d, tree, counts, unsorted_names)

    items, perf_folders, categories = [], {}, {}
    for d in tops:
        if role_of[d] in (ROLE_CATEGORY, ROLE_CATEGORY_PERFORMERS):
            categories[d] = {'name': d, 'rel': d, 'path': os.path.join(root, d), 'role': role_of[d]}
        if role_of[d] in (ROLE_PERFORMERS, ROLE_CATEGORY_PERFORMERS):
            for child in tree[d]['dirs']:
                if _is_unsorted_name(child, unsorted_names) or _norm(child) in MEDIA_DIR_NAMES:
                    continue
                rel = os.path.join(d, child)
                perf_folders[rel] = {'rel': rel, 'name': child, 'path': os.path.join(root, rel),
                                     'container': d, 'containerRole': role_of[d],
                                     'videos': counts.get(rel, 0), 'performer': None}

    # Tipologie aggiunte a mano nel wizard: possono non esistere ancora su disco,
    # vengono create quando l'utente conferma gli spostamenti.
    for extra in (extra_categories or []):
        rel = str(extra or '').strip().strip('\\/')
        if not rel or rel in categories:
            continue
        categories[rel] = {'name': rel, 'rel': rel, 'path': os.path.join(root, rel), 'role': ROLE_CATEGORY}

    for rel, node in tree.items():
        if not node['files']:
            continue
        parts = rel.split(os.sep) if rel else []
        ctx = _context(parts, role_of, unsorted_names)
        if ctx is None:
            continue
        for fn in node['files']:
            path = os.path.join(root, rel, fn) if rel else os.path.join(root, fn)
            items.append({'path': path, 'rel': os.path.join(rel, fn) if rel else fn, 'dir': rel,
                          'name': fn, 'ctx': ctx})
    return {'root': root, 'tree': tree, 'counts': counts, 'roles': role_of, 'items': items,
            'perfFolders': perf_folders, 'categories': categories, 'unsortedNames': unsorted_names}


def cmd_detect(args):
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        # exit 0: _runOneShot scarta l'output dei processi usciti con errore,
        # e l'utente vedrebbe "exit 1" invece del motivo.
        _emit({'ok': False, 'error': 'cartella non trovata: ' + root})
        return 0
    roles = json.loads(args.roles) if args.roles else None
    lib = build_library(root, roles)
    folders = []
    for d in lib['tree']['']['dirs']:
        unsorted = sum(1 for it in lib['items'] if it['rel'].startswith(d + os.sep) and it['ctx']['unsorted'])
        folders.append({'name': d, 'role': lib['roles'][d], 'videos': lib['counts'].get(d, 0),
                        'direct': len(lib['tree'][d]['files']),
                        'performerFolders': sum(1 for p in lib['perfFolders'].values() if p['container'] == d),
                        'unsorted': unsorted,
                        'children': lib['tree'][d]['dirs'][:8], 'childCount': len(lib['tree'][d]['dirs'])})
    containers = [f for f in folders if f['role'] == ROLE_PERFORMERS]
    containers.sort(key=lambda f: -f['videos'])
    _emit({'ok': True, 'root': root, 'total': lib['counts'].get('', 0), 'folders': folders,
           'rootFiles': len(lib['tree']['']['files']),
           'unsortedTotal': sum(1 for it in lib['items'] if it['ctx']['unsorted']),
           'performerFolders': len(lib['perfFolders']),
           'performerContainer': containers[0]['name'] if containers else None,
           'unsortedNames': DEFAULT_UNSORTED_NAMES})
    return 0


# ─────────────────────────────────────────────────────────────────────
# Nome file → titolo, codici, parole, finestre-nome
# ─────────────────────────────────────────────────────────────────────
_EXT_ANY = re.compile(r'\.(?:' + '|'.join(sorted(e[1:] for e in VIDEO_EXTS)) + r')\b', re.I)
_BRACKET_RX = re.compile(r'\[([^\]]*)\]')
_HASHTAG_RX = re.compile(r'#([^\s#\[\]]+)')
_URL_RX = re.compile(r'https?://\S+|\bwww\.\S+', re.I)
_SITE_RX = re.compile(r'(?<![\w.])(?:www\.)?[a-z0-9][a-z0-9-]{1,30}\.(?:com|net|org|xxx|club|porn|tv)(?![\w])', re.I)
_RES_RX = re.compile(r'\b(?:2160|1440|1080|720|576|540|480|360|240)[pi]?\b|\b[48]k\b', re.I)
_DATE_RXS = [
    (re.compile(r'\b((?:19|20)\d\d)[.\-_ ](\d{1,2})[.\-_ ](\d{1,2})\b'), (1, 2, 3)),
    (re.compile(r'\b(\d{1,2})[.\-_ ](\d{1,2})[.\-_ ]((?:19|20)\d\d)\b'), (3, 2, 1)),
    (re.compile(r'(?<!\d)(\d\d)\.(\d\d)\.(\d\d)(?![\d])'), (1, 2, 3)),
]
_CODE_RX = re.compile(r'(?<![A-Za-z0-9])([A-Z]{2,4})[-_ ]?(\d{2,5})(?![A-Za-z0-9])')
_CODE_FULL = re.compile(r'[A-Z]{2,4}\d{2,5}')
_CODE_BLOCK = {'MP', 'AC', 'DTS', 'AAC', 'HDR', 'UHD', 'FHD', 'HD', 'SD', 'AV', 'VP', 'DDP', 'PPV', 'WEB',
               'HEVC', 'FPS', 'VOL', 'EP', 'PT', 'OF', 'BBC', 'DAP', 'DVP', 'TAP', 'DP', 'ATM', 'POV',
               'MMF', 'FFM', 'CD', 'DVD', 'XXX', 'VR', 'AI', 'NO', 'TOP', 'BEST', 'PART', 'SEX', 'HOT'}
_NUM_ON_RX = re.compile(r'\b(\d{1,2})\s*on\s*(\d)\b', re.I)
_NUM_MEN_RX = re.compile(r'\b(\d{1,2})\s*(?:bbcs?|guys|men|man|cocks|dicks|loads|studs|black cocks)\b', re.I)
_SPAM_RX = re.compile(
    r'\b(?:find your bad girl here|try\s*=_?|to see videos that are deleted from this site and more|'
    r'visit|full video|download|watch|link)\b', re.I)
_CAMEL_RX = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+')
_CAMEL_OK = re.compile(r'^(?:[a-z]*(?:[A-Z][a-z]+)+|[A-Z]+[a-z]*|[a-z]+|[A-Z][a-z]+)$')

SITE_WORDS = {'eporner', 'pornhub', 'xvideos', 'xhamster', 'spankbang', 'xnxx', 'redtube', 'youporn',
              'txxx', 'hqporner', 'porntrex', 'nsxyprn', 'sxyprn', 'streamtape', 'doodstream', 'bigwarp',
              'uploadmall', 'mikess', 'iluvy', 'https', 'http', 'www', 'com', 'net', 'org', 'club', 'io',
              'latest', 'release', 'newpornupdates', 'pornogames', 'hotgirls', 'legalporno', 'analvids',
              'upscaled', 'enhanced', '60fps', 'fps', 'hd', 'fullhd', 'uhd', 'sd', 'mp4', 'mkv', 'ppv'}
STOP_TOKENS = {'the', 'a', 'an', 'and', 'of', 'in', 'on', 'with', 'to', 'for', 'her', 'his', 'by', 'is', 'at',
               'from', 'get', 'gets', 'vs', 'it', 'this', 'that', 'my', 'your', 'she', 'he', 'me', 'you',
               'new', 'full', 'video', 'scene', 'part', 'ep', 'vol'}
# Parole che compaiono in qualunque titolo: nel classificatore aggiungono solo rumore
# (misurato: "She Wants Give Her…" finiva in una tipologia con fiducia "probabile").
GENERIC_TOKENS = {'him', 'they', 'them', 'we', 'us', 'our', 'their', 'its', 'these', 'those', 'want', 'give',
                  'take', 'make', 'like', 'can', 'will', 'just', 'very', 'so', 'too', 'all', 'some', 'any',
                  'one', 'two', 'three', 'first', 'good', 'best', 'hot', 'sexy', 'cute', 'nice', 'great',
                  'amazing', 'beautiful', 'gorgeous', 'super', 'real', 'episode', 'clip', 'xxx', 'porn',
                  'sex', 'free', 'here', 'there', 'what', 'when', 'who', 'how', 'now', 'out', 'up'}

# Prime parole che non aprono mai il nome di un performer: servono solo a non
# interrogare StashDB su "Big Rose" o "Hard Sex". La verifica vera resta il
# confronto esatto con nome/alias restituiti da StashDB.
VOCAB = set('''
about after again all alone amateur amazing anal and another any are around as ass at ate babe babes baby back
bad balls bang banged bangs bareback bathroom be beach beautiful bed bedroom before best big bigass bigtits
bikini bitch black blonde blowbang blowjob blowjobs blue bondage boss both boy boyfriend boys brazilian
breast brother brunette brutal bubble bukkake busty but butt by cam can casting caught cheating chick chubby
classic close college come compilation cosplay couple cowgirl crazy cream creampie cuckold cum cumshot
cumshots curvy cute czech dap daddy date day deep deeper deepthroat destroyed dick dicks dildo dirty do does
doggy dominated double down dp dream dress drink drinking dvp ebony enjoy enjoys epic euro european every
exclusive extreme face facial fake family fantasy fat feet fetish fingering first fisting fit for french
fresh friend friends from fuck fucked fucking full fun gangbang gape gapes gaping gay german get gets getting
girl girlfriend girls glory gloryhole go goes gonna good gorgeous goth granny great group guy guys hairy hand
handjob happy hard hardcore has have he her here hidden high his home homemade horny hot hotel hotwife house
housewife how huge i ice in indian insane inside interracial into is it italian its japanese just kinky kiss
kitchen lady large last latin latina leather legal lesbian let lets licking like lingerie little live long
love loves lucky made maid make makes man massage masturbation mature me men milf mind mini mix mom monster
more morning most mouth mr ms much my naked nasty natural naughty new next nice night no not now nude nurse
nympho of office oh oil oiled ok old on one only open oral orgasm orgy our out outdoor part party passion
perfect perv petite pick piss pissing pov pregnant pretty private public punish pussy queen quick real
reality red redhead rides riding rough round russian scene secret seduced sex sexy she shemale shower shy
sister skinny slut sluts slutty small smoking so solo some spanish sperm squirt squirting stepdad stepmom
stepsis stepsister stockings strapon stranger street student stuffed stunning submissive super surprise
swallow swallows sweet take takes taking tattoo tattooed teacher teen teens thai that the their them then
there these they thick thin this three threesome throat time tiny tits to toy toys trans triple try two ugly
under uniform very video vintage vs wants wet what when white who wife wild with woman women work xxx yoga
young your zero watch onlyfans legalporno wet
la il lo una uno un con che per del della dei nel sul troia culo cazzo figa scopata pompino sborra moglie
ragazza italiana italiano amatoriale porca puttana zoccola inculata bionda mora matura giovane essa ela meu
minha uma com para puta gostosa novinha casada follando chica madura colegiala
'''.split())
_VOCAB_STEMS = {_stem(w) for w in VOCAB}
NAME_PARTICLES = {'de', 'da', 'di', 'del', 'della', 'van', 'von', 'der', 'den', 'la', 'le', 'dos', 'das', 'du', 'st'}
VERBS = set('''plays play gets takes take fucks loves enjoys sucks suck rides ride meets tries wants needs shows
teases gives give goes does has have is are was be becomes finds learns likes makes uses screws bangs drills
pounds slams swallows drinks eats licks worships sits cums squirts comes joins invites visits watches explores
mounts'''.split())
_MID_STOP = STOP_TOKENS | VERBS


def _deleet(tok):
    # "S0fia Sm1th": i nomi offuscati per aggirare le rimozioni tornano leggibili.
    if 4 <= len(tok) <= 8:
        digits = [i for i, c in enumerate(tok) if c.isdigit()]
        if 1 <= len(digits) <= 2 and all(0 < i < len(tok) - 1 and tok[i - 1].isalpha() and tok[i + 1].isalpha()
                                         for i in digits):
            return tok.translate(str.maketrans('013457', 'oieast'))
    return tok


def _is_idlike(tok):
    t = tok.strip("'-")
    if not t:
        return True
    if _CODE_FULL.fullmatch(t) or re.fullmatch(r'\d{1,2}on\d', t, re.I):
        return False
    if len(t) >= 5 and re.search(r'\d', t) and re.search(r'[A-Za-z]', t):
        return True
    if len(t) >= 12 and re.fullmatch(r'[0-9a-fA-F]+', t):
        return True
    if len(t) >= 8 and t.isalpha():
        if not _CAMEL_OK.match(t):
            return True
        # "yYzkAsUifEk": maiuscole sparse che imitano il CamelCase. Le parole vere
        # composte ("DoublePenetration", "AnnaDeVille") hanno segmenti lunghi.
        segs = _CAMEL_RX.findall(t)
        if len(segs) >= 4 and sum(len(s) for s in segs) / float(len(segs)) < 3.0:
            return True
    return False


def _find_date(s):
    for rx, (yi, mi, di) in _DATE_RXS:
        m = rx.search(s)
        if not m:
            continue
        y, mo, d = m.group(yi), int(m.group(mi)), int(m.group(di))
        if len(y) == 2:
            y = '20' + y
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return '%s-%02d-%02d' % (y, mo, d), m.span()
    return None, None


def parse_filename(filename):
    base = _EXT_ANY.sub(' ', filename)
    try:
        base = urllib.parse.unquote(base)
    except Exception:
        pass
    codes = []
    for m in _CODE_RX.finditer(base):
        if m.group(1) not in _CODE_BLOCK:
            code = m.group(1) + m.group(2)
            if code not in codes:
                codes.append(code)
    hexname = bool(re.fullmatch(r'[0-9a-fA-F]{16,}(?:\s*\(\d+\))?', base.strip()))
    tags = []

    def _bracket(m):
        for w in re.split(r'[\s,;|]+', m.group(1)):
            if w and not _is_idlike(w):
                tags.append(w)
        return ' '

    s = _BRACKET_RX.sub(_bracket, base)
    s = _HASHTAG_RX.sub(lambda m: (tags.append(m.group(1)), ' ')[1], s)
    s = _URL_RX.sub(' ', s)
    s = _SITE_RX.sub(' ', s)
    s = _SPAM_RX.sub(' ', s)
    date, span = _find_date(s)
    if span:
        s = s[:span[0]] + ' ' + s[span[1]:]
    specials = set()
    if _NUM_ON_RX.search(s):
        specials.add('x:non1')
    if _NUM_MEN_RX.search(s):
        specials.add('x:nmen')
    s = _RES_RX.sub(' ', s)
    s = re.sub(r'[._]+', ' ', s)
    s = re.sub(r'[()\[\]{}"“”«»|~^*+=<>/\\:;!?,]+', ' ', s)
    s = re.sub(r'\s*[-–—]+\s*', ' - ', s)

    words, seps = [], set()
    for raw in s.split():
        if raw == '-':
            seps.add(len(words))
            continue
        w = _deleet(raw.strip("'-&@%$"))
        if not w or w.isdigit() or _is_idlike(w) or w.upper() in codes:
            continue
        if _squash(w) in SITE_WORDS and not re.fullmatch(r'\d{1,2}on\d', w, re.I):
            continue
        words.append(w)

    tokens = set(specials)
    for w in words + tags:
        for part in _CAMEL_RX.findall(w):
            t = _stem(_strip_accents(part).lower())
            if len(t) >= 2 and not t.isdigit() and t not in STOP_TOKENS and t not in SITE_WORDS \
                    and t not in GENERIC_TOKENS:
                tokens.add('w:' + t)
    if hexname:
        tokens.add('shape:hex')

    ordered = []
    for w in words:
        for part in re.split(r"[^A-Za-z']+", _strip_accents(w)):
            if part:
                ordered.append(part.lower().strip("'"))
    windows = []
    seen = set()
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            win = words[i:i + n]
            if not all(re.fullmatch(r"[^\W\d_]+(?:['-][^\W\d_]+)?", x) for x in win):
                continue
            first, last = _strip_accents(win[0]), _strip_accents(win[-1])
            if len(first) < 3 or first.lower() in VOCAB or re.fullmatch(r'[mfMF]{3,6}', first) \
                    or (first.isupper() and len(first) <= 3):
                continue
            if last.lower() in _MID_STOP or (last.isupper() and len(last) <= 3) or any(
                    _strip_accents(x).lower() in _MID_STOP for x in win[1:-1] if x.lower() not in NAME_PARTICLES):
                continue
            titled = all(x[0].isupper() or x.lower() in NAME_PARTICLES for x in win)
            # "Nicole Black", "Ally Wild": un cognome può essere una parola comune,
            # ma solo dentro un nome scritto con le maiuscole.
            if last.lower() in VOCAB and not titled:
                continue
            sq = _squash(' '.join(win))
            if len(sq) < 6 or sq in seen:
                continue
            seen.add(sq)
            score = (2 if titled else 0) + (1 if (i == 0 or i in seps) else 0) + (1 if n == 2 else 0) \
                - (1 if last.lower() in VOCAB else 0)
            windows.append({'text': ' '.join(win), 'squash': sq, 'score': score, 'pos': i})
    windows.sort(key=lambda x: (-x['score'], x['pos']))

    title_words = [w for w in words if _squash(w) not in SITE_WORDS]
    return {'title': ' '.join(title_words[:14]).strip(), 'codes': codes, 'date': date, 'tokens': tokens,
            'ordered': ordered, 'windows': windows[:4], 'squash': _squash(' '.join(words + tags)),
            'hexname': hexname}


# ─────────────────────────────────────────────────────────────────────
# Cache persistente (userData)
# ─────────────────────────────────────────────────────────────────────
class Cache:
    def __init__(self, path):
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.con = sqlite3.connect(path, timeout=30)
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, oshash TEXT)')
        self.con.execute('CREATE TABLE IF NOT EXISTS vhash(oshash TEXT PRIMARY KEY, phash TEXT, duration REAL, updated INTEGER)')
        self.con.execute('CREATE TABLE IF NOT EXISTS lookup(kind TEXT, key TEXT, value TEXT, updated INTEGER, PRIMARY KEY(kind, key))')
        self.con.execute('CREATE TABLE IF NOT EXISTS clipemb(oshash TEXT PRIMARY KEY, vec BLOB, updated INTEGER)')
        self.con.commit()

    def oshash(self, path, size, mtime):
        r = self.con.execute('SELECT size, mtime, oshash FROM files WHERE path=?', (path,)).fetchone()
        return r[2] if r and r[0] == size and abs((r[1] or 0) - mtime) < 1e-3 else None

    def put_oshash(self, path, size, mtime, h):
        self.con.execute('INSERT OR REPLACE INTO files(path, size, mtime, oshash) VALUES(?,?,?,?)', (path, size, mtime, h))

    def vhash(self, oshash):
        r = self.con.execute('SELECT phash, duration FROM vhash WHERE oshash=?', (oshash,)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def put_vhash(self, oshash, phash=None, duration=None):
        self.con.execute(
            'INSERT INTO vhash(oshash, phash, duration, updated) VALUES(?,?,?,?) '
            'ON CONFLICT(oshash) DO UPDATE SET phash=COALESCE(excluded.phash, vhash.phash), '
            'duration=COALESCE(excluded.duration, vhash.duration), updated=excluded.updated',
            (oshash, phash, duration, int(time.time())))

    def clipemb(self, oshash):
        """None = da calcolare, altrimenti il vettore. Un fallimento (b'') vale una
        settimana: il file poteva essere solo occupato o su un disco staccato."""
        r = self.con.execute('SELECT vec, updated FROM clipemb WHERE oshash=?', (oshash,)).fetchone()
        if not r or (not r[0] and time.time() - (r[1] or 0) > 7 * DAY):
            return None
        return r[0]

    def put_clipemb(self, oshash, blob):
        self.con.execute('INSERT OR REPLACE INTO clipemb(oshash, vec, updated) VALUES(?,?,?)',
                         (oshash, blob, int(time.time())))

    def lookup(self, kind, key, ttl_hit, ttl_miss):
        r = self.con.execute('SELECT value, updated FROM lookup WHERE kind=? AND key=?', (kind, key)).fetchone()
        if not r:
            return False, None
        value = json.loads(r[0])
        ttl = ttl_hit if value else ttl_miss
        if time.time() - (r[1] or 0) > ttl:
            return False, None
        return True, value

    def put_lookup(self, kind, key, value):
        self.con.execute('INSERT OR REPLACE INTO lookup(kind, key, value, updated) VALUES(?,?,?,?)',
                         (kind, key, json.dumps(value, ensure_ascii=False), int(time.time())))

    def commit(self):
        try:
            self.con.commit()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# StashDB
# ─────────────────────────────────────────────────────────────────────
_SCENE_FIELDS = '''id title code release_date duration
  studio { name parent { name } }
  performers { as performer { id name disambiguation gender aliases } }
  tags { name }'''
Q_FP = ('query($fps:[[FingerprintQueryInput!]!]!){ findScenesBySceneFingerprints(fingerprints:$fps){ '
        + _SCENE_FIELDS + ' fingerprints { hash algorithm submissions } } }')
Q_SCENE = 'query($t:String!,$l:Int!){ searchScene(term:$t, limit:$l){ ' + _SCENE_FIELDS + ' } }'
Q_PERF = ('query($t:String!,$l:Int!){ searchPerformer(term:$t, limit:$l){ '
          'id name disambiguation gender aliases scene_count } }')


def _norm_scene(s):
    if not s:
        return None
    studio = s.get('studio') or {}
    perfs = []
    for pp in s.get('performers') or []:
        p = pp.get('performer') or {}
        if p.get('name'):
            perfs.append({'id': p.get('id'), 'name': p['name'], 'gender': p.get('gender'),
                          'aliases': p.get('aliases') or [], 'disambiguation': p.get('disambiguation')})
    return {'id': s.get('id'), 'title': s.get('title') or '', 'code': s.get('code') or '',
            'date': s.get('release_date') or '', 'duration': s.get('duration') or 0,
            'studio': studio.get('name') or '', 'network': (studio.get('parent') or {}).get('name') or '',
            'performers': perfs, 'tags': [t['name'] for t in s.get('tags') or [] if t.get('name')]}


class Stash:
    def __init__(self, cache, enabled):
        self.cache, self.enabled, self.requests, self.errors = cache, bool(enabled), 0, 0
        self.disabled_reason, self._last, self._mod = None, 0.0, None
        if not self.enabled:
            return
        try:
            import stashdb
            self._mod = stashdb
            if not stashdb._load_key():
                self.disable('Chiave API StashDB mancante: impostala in Impostazioni › AI')
        except Exception as e:
            self.disable('stashdb.py non disponibile: %s' % e)

    def disable(self, reason):
        if self.enabled:
            _warn(reason)
        self.enabled, self.disabled_reason = False, reason

    def _gql(self, query, variables, timeout=40):
        if not self.enabled:
            return None
        for attempt in range(3):
            wait = 0.12 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.requests += 1
            data, err = self._mod._gql(query, variables, timeout=timeout)
            if data is not None:
                self.errors = 0
                return data
            e = err or ''
            if 'HTTP 401' in e or 'HTTP 403' in e or 'Manca API key' in e:
                self.disable('Chiave StashDB rifiutata (%s)' % e[:80])
                return None
            if not e.startswith('HTTP') and not re.search(
                    r'timed? ?out|urlopen|errno|getaddrinfo|ssl|connection|eof|reset', e, re.I):
                # Errore GraphQL: la query è stata rifiutata, ripeterla non cambia l'esito.
                _warn('StashDB: %s' % e[:160])
                break
            time.sleep((6 if 'HTTP 429' in e else 1.5) * (attempt + 1))
        self.errors += 1
        if self.errors >= 6:
            self.disable('StashDB non raggiungibile: proseguo senza')
        return None

    def by_fingerprints(self, algo, hashes, on_batch=None):
        out, todo = {}, []
        for h in hashes:
            hit, val = self.cache.lookup('fp:' + algo, h, 90 * DAY, 7 * DAY)
            if hit:
                out[h] = val
            else:
                todo.append(h)
        if on_batch:
            on_batch(len(hashes) - len(todo))
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            data = self._gql(Q_FP, {'fps': [[{'hash': h, 'algorithm': algo}] for h in chunk]}, timeout=90)
            if data is None:
                break
            for h, scenes in zip(chunk, data.get('findScenesBySceneFingerprints') or []):
                sc = _pick_fp_scene(scenes or [], h, algo)
                self.cache.put_lookup('fp:' + algo, h, sc)
                out[h] = sc
            self.cache.commit()
            if on_batch:
                on_batch(len(chunk))
        return out

    def search_scene(self, term, limit=6):
        term = (term or '').strip()[:160]
        if not term:
            return []
        key = '%d:%s' % (limit, term.lower())
        hit, val = self.cache.lookup('scene', key, 30 * DAY, 14 * DAY)
        if hit:
            return val or []
        data = self._gql(Q_SCENE, {'t': term, 'l': limit})
        if data is None:
            return []
        res = [_norm_scene(s) for s in data.get('searchScene') or [] if s]
        self.cache.put_lookup('scene', key, res)
        return res

    def search_performer(self, term, limit=5):
        term = (term or '').strip()[:80]
        if not term:
            return []
        key = '%d:%s' % (limit, term.lower())
        hit, val = self.cache.lookup('perf', key, 60 * DAY, 30 * DAY)
        if hit:
            return val or []
        data = self._gql(Q_PERF, {'t': term, 'l': limit})
        if data is None:
            return []
        res = [{'id': p.get('id'), 'name': p.get('name'), 'gender': p.get('gender'),
                'aliases': p.get('aliases') or [], 'disambiguation': p.get('disambiguation'),
                'scene_count': p.get('scene_count') or 0} for p in data.get('searchPerformer') or [] if p]
        self.cache.put_lookup('perf', key, res)
        return res


def _pick_fp_scene(scenes, h, algo):
    if not scenes:
        return None

    def score(s):
        best_dist, subs = 64, 0
        for fp in s.get('fingerprints') or []:
            if fp.get('algorithm') != algo:
                continue
            d = 0 if fp.get('hash') == h else (videohash.hamming(fp.get('hash'), h) if algo == 'PHASH' else 64)
            if d < best_dist or (d == best_dist and (fp.get('submissions') or 0) > subs):
                best_dist, subs = d, fp.get('submissions') or 0
        return (best_dist, -subs)

    return _norm_scene(min(scenes, key=score))


# ─────────────────────────────────────────────────────────────────────
# Verifiche di coerenza
# ─────────────────────────────────────────────────────────────────────
def _dur_ok(a, b, tol):
    return bool(a and b) and abs(a - b) <= max(4.0, tol * b)


def _content_tokens(text):
    return {_stem(t) for t in _norm(text).split() if len(t) >= 3 and t not in STOP_TOKENS}


def _containment(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def _names_of(p):
    return [p.get('name') or ''] + list(p.get('aliases') or [])


def _in_name(p, squash_blob):
    return any(len(_squash(n)) >= 6 and _squash(n) in squash_blob for n in _names_of(p))


# ─────────────────────────────────────────────────────────────────────
# Classificatore tipologia
# ─────────────────────────────────────────────────────────────────────
class Classifier:
    def __init__(self, alpha=0.5, prior_weight=0.3):
        self.alpha, self.prior_weight = alpha, prior_weight

    def fit(self, X, y):
        self.labels = sorted(set(y))
        n = Counter(y)
        total = float(len(y))
        self.fc = {l: Counter() for l in self.labels}
        tot = Counter()
        vocab = set()
        for feats, l in zip(X, y):
            self.fc[l].update(feats)
            tot[l] += len(feats)
            vocab.update(feats)
        self.vocab = vocab
        V = len(vocab) + 1
        self.logprior = {l: self.prior_weight * math.log(n[l] / total) for l in self.labels}
        self.logden = {l: math.log(tot[l] + self.alpha * V) for l in self.labels}
        self.logalpha = math.log(self.alpha)
        return self

    def predict(self, feats):
        feats = [f for f in feats if f in self.vocab]
        if not feats or not self.labels:
            return None, 0.0, 0
        scores = []
        for l in self.labels:
            c, den = self.fc[l], self.logden[l]
            s = self.logprior[l]
            for f in feats:
                v = c.get(f)
                s += (math.log(v + self.alpha) if v else self.logalpha) - den
            scores.append((s, l))
        scores.sort(reverse=True)
        margin = scores[0][0] - (scores[1][0] if len(scores) > 1 else scores[0][0] - 10)
        return scores[0][1], margin / math.sqrt(len(feats)), len(feats)


def calibrate(X, y, folds=5, seed=13):
    """Soglie di margine per precisione ≥90% e ≥70%, stimate in cross-validation."""
    if len(y) < 30 or len(set(y)) < 2:
        return None
    idx = list(range(len(y)))
    random.Random(seed).shuffle(idx)
    preds = []
    for k in range(folds):
        test = idx[k::folds]
        tset = set(test)
        train = [i for i in idx if i not in tset]
        clf = Classifier().fit([X[i] for i in train], [y[i] for i in train])
        for i in test:
            lab, m, _ = clf.predict(X[i])
            if lab is not None:
                preds.append((m, lab == y[i]))
    if not preds:
        return None
    preds.sort(key=lambda p: -p[0])

    def threshold(target):
        best, correct, cov = None, 0, 0
        for n, (m, ok) in enumerate(preds, 1):
            correct += ok
            if n >= 10 and correct / float(n) >= target:
                best, cov = m, n
        return best, cov

    t90, c90 = threshold(0.90)
    t70, c70 = threshold(0.70)
    acc = sum(ok for _, ok in preds) / float(len(preds))
    return {'t90': t90, 't70': t70, 'coverage90': round(c90 / float(len(y)), 3),
            'coverage70': round(c70 / float(len(y)), 3), 'accuracy': round(acc, 3), 'samples': len(y)}


def train_visual(X, y, labels, seed=7):
    """Regressione logistica sui vettori CLIP, con soglie calibrate in validazione
    incrociata: il riconoscimento dalle immagini parla solo quando è sicuro.
    Misurato: sopra la soglia del 90% copre un terzo dei file col 97% di precisione."""
    import numpy as np, torch

    def fit(Xtr, ytr, k, epochs=300):
        w = torch.zeros(Xtr.shape[1], k, requires_grad=True)
        b = torch.zeros(k, requires_grad=True)
        opt = torch.optim.Adam([w, b], lr=0.05, weight_decay=1e-4)
        xt, yt = torch.tensor(Xtr), torch.tensor(ytr)
        for _ in range(epochs):
            opt.zero_grad()
            torch.nn.functional.cross_entropy(xt @ w + b, yt).backward()
            opt.step()
        return w.detach(), b.detach()

    k = len(labels)
    idx = np.arange(len(y))
    np.random.RandomState(seed).shuffle(idx)
    preds = []
    for f in np.array_split(idx, 5):
        tr = np.setdiff1d(idx, f)
        W, B = fit(X[tr], y[tr], k)
        with torch.no_grad():
            p = torch.softmax(torch.tensor(X[f]) @ W + B, 1).numpy()
        for i, row in zip(f, p):
            preds.append((float(row.max()), int(row.argmax()) == int(y[i])))
    preds.sort(key=lambda t: -t[0])

    def threshold(target):
        best, correct = None, 0
        for n, (p, ok) in enumerate(preds, 1):
            correct += ok
            if n >= 20 and correct / float(n) >= target:
                best = p
        return best

    W, B = fit(X, y, k)
    acc = sum(ok for _, ok in preds) / float(max(1, len(preds)))
    t90, t70 = threshold(0.90), threshold(0.70)
    cov = (sum(1 for p, _ in preds if t90 is not None and p >= t90) / float(max(1, len(preds))))
    return {'W': W, 'B': B, 'labels': labels, 't90': t90, 't70': t70,
            'accuracy': round(acc, 3), 'coverage90': round(cov, 3), 'samples': int(len(y))}


def features(item):
    f = set(item['parsed']['tokens'])
    sc = item.get('scene')
    if sc:
        for tag in sc['tags']:
            f.add('t:' + _norm(tag))
            for w in _norm(tag).split():
                if len(w) >= 2 and w not in STOP_TOKENS:
                    f.add('w:' + _stem(w))
        if sc['studio']:
            f.add('s:' + _norm(sc['studio']))
        males = sum(1 for p in sc['performers'] if (p.get('gender') or '') in MALE_GENDERS)
        f.add('m:%d' % min(males, 5))
    for p in item.get('performers') or []:
        f.add('p:' + _squash(p['name']))
    return f


def literal_category(item, categories):
    words = {t[2:] for t in item['parsed']['tokens'] if t.startswith('w:')}
    sc = item.get('scene')
    if sc:
        for tag in sc['tags']:
            words.update(_stem(w) for w in _norm(tag).split())
    squash = item['parsed']['squash']
    best, best_n = None, 0
    for c in categories:
        cw = [_stem(w) for w in _norm(c).split() if w not in STOP_TOKENS]
        if not cw:
            continue
        if all(w in words for w in cw) or (len(_squash(c)) >= 6 and _squash(c) in squash):
            if len(cw) > best_n:
                best, best_n = c, len(cw)
            elif len(cw) == best_n:
                best = None
    return best


# ─────────────────────────────────────────────────────────────────────
# Analisi
# ─────────────────────────────────────────────────────────────────────
def _merge_performer(item, p, source, conf):
    key = p.get('id') or _squash(p.get('name'))
    for q in item['performers']:
        if q['key'] == key:
            if CONF_RANK[conf] > CONF_RANK[q['conf']]:
                q['conf'], q['source'] = conf, source
            return q
    q = {'key': key, 'id': p.get('id'), 'name': p.get('name'), 'gender': p.get('gender'),
         'aliases': p.get('aliases') or [], 'source': source, 'conf': conf,
         'inName': _in_name(p, item['parsed']['squash'])}
    item['performers'].append(q)
    return q


def _set_scene(item, scene, source, conf):
    item['scene'], item['sceneSource'], item['sceneConf'] = scene, source, conf
    for p in scene['performers']:
        _merge_performer(item, p, source, conf)


class Analyzer:
    def __init__(self, cfg, cache_path, ffmpeg=None):
        self.cfg = cfg
        self.root = os.path.abspath(cfg['root'])
        self.layout = cfg.get('layout') if cfg.get('layout') in LAYOUTS else 'current'
        self.scope = cfg.get('scope') or 'unsorted'
        self.min_perf = max(1, int(cfg.get('minPerformerFiles') or 5))
        self.cache = Cache(cache_path)
        self.stash = Stash(self.cache, cfg.get('useStashdb', True))
        self.use_phash = bool(cfg.get('usePhash', True))
        self.use_search = bool(cfg.get('useSearch', True))
        self.ffmpeg = videohash.resolve_ffmpeg(ffmpeg)
        self.t0 = time.time()
        self.stats = Counter()
        self.vis = None

    # ── scansione ──
    def scan(self):
        _emit({'type': 'phase', 'phase': 'scan', 'text': 'Lettura della libreria…'})
        lib = build_library(self.root, self.cfg.get('roles') or {}, self.cfg.get('unsortedNames'),
                            self.cfg.get('extraCategories'))
        self.lib = lib
        items = lib['items']
        for i, it in enumerate(items):
            it['id'] = i
            it['parsed'] = parse_filename(it['name'])
            it['performers'] = []
            it['scene'] = None
            it['sceneSource'] = it['sceneConf'] = None
            it['inScope'] = it['ctx']['unsorted'] if self.scope == 'unsorted' else True
            try:
                st = os.stat(it['path'])
                it['size'], it['mtime'] = st.st_size, st.st_mtime
            except OSError:
                it['size'], it['mtime'] = 0, 0
            it['oshash'] = it['phash'] = it['duration'] = None
        self.items = [it for it in items if it['size'] > 0]
        self.scope_items = [it for it in self.items if it['inScope']]
        _emit({'type': 'phase', 'phase': 'scan', 'text': '%d video, %d da organizzare' % (
            len(self.items), len(self.scope_items)), 'total': len(self.items)})

    # ── OSHASH ──
    def hash_files(self):
        # Le impronte servono anche senza StashDB: trovano i doppioni e fanno da
        # chiave stabile per la cache, quindi si calcolano sempre (e restano).
        targets = self.items
        if not targets:
            return
        prog = Progress('hash', len(targets), 'Impronte dei file…')
        todo = []
        for it in targets:
            h = self.cache.oshash(it['path'], it['size'], it['mtime'])
            if h:
                it['oshash'] = h
            else:
                todo.append(it)
        done = len(targets) - len(todo)
        prog.tick(done, force=True)

        def work(it):
            try:
                return it, videohash.oshash(it['path'])
            except Exception:
                return it, None

        with ThreadPoolExecutor(8) as ex:
            for it, h in ex.map(work, todo):
                done += 1
                if h:
                    it['oshash'] = h
                    self.cache.put_oshash(it['path'], it['size'], it['mtime'], h)
                if done % 200 == 0:
                    self.cache.commit()
                prog.tick(done, file=it['name'])
        self.cache.commit()
        prog.tick(len(targets), force=True)

    def fingerprint_lookup(self):
        hashed = [it for it in self.items if it['oshash']]
        if not hashed or not self.stash.enabled:
            return
        prog = Progress('stash', len(hashed), 'Confronto impronte con StashDB…')
        state = {'done': 0}

        def on_batch(n):
            state['done'] += n
            prog.tick(min(state['done'], len(hashed)))

        res = self.stash.by_fingerprints('OSHASH', sorted({it['oshash'] for it in hashed}), on_batch)
        found = 0
        for it in hashed:
            sc = res.get(it['oshash'])
            if sc:
                _set_scene(it, sc, 'oshash', 'certain')
                found += 1
                if it['inScope']:
                    self.stats['oshash'] += 1
        prog.tick(len(hashed), found=found, force=True)

    # ── cartelle performer ↔ StashDB ──
    def map_performer_folders(self):
        folders = list(self.lib['perfFolders'].values())
        if not folders:
            return
        prog = Progress('folders', len(folders), 'Collego le cartelle performer…')
        by_folder = defaultdict(list)
        for it in self.items:
            pf = it['ctx']['perf_folder']
            if pf:
                by_folder[pf].append(it)
        for n, f in enumerate(folders, 1):
            evidence = Counter()
            perf_by_key = {}
            matched = 0
            for it in by_folder.get(f['rel'], []):
                if not it['scene']:
                    continue
                matched += 1
                for p in it['scene']['performers']:
                    if (p.get('gender') or '') in MALE_GENDERS:
                        continue
                    evidence[p['id']] += 1
                    perf_by_key[p['id']] = p
            ev = None
            if evidence:
                pid, cnt = evidence.most_common(1)[0]
                if cnt >= 2 and cnt >= 0.5 * matched:
                    ev = perf_by_key[pid]
            by_name = None
            fsq = _squash(f['name'])
            if self.stash.enabled and len(fsq) >= 3:
                cands = self.stash.search_performer(f['name'], 8)
                exact = [p for p in cands if any(_squash(x) == fsq for x in _names_of(p))]
                if exact:
                    by_name = max(exact, key=lambda p: p.get('scene_count') or 0)
                elif len(fsq) >= 8:
                    fuzzy = [(difflib.SequenceMatcher(None, fsq, _squash(p['name'])).ratio(), p) for p in cands
                             if (p.get('scene_count') or 0) >= 5]
                    fuzzy = [x for x in fuzzy if x[0] >= 0.86]
                    if fuzzy:
                        by_name = max(fuzzy, key=lambda x: x[0])[1]
            if by_name and len(f['name'].split()) == 1 and not (ev and ev.get('id') == by_name.get('id')):
                # Un nome singolo ("Isabella") corrisponde a troppe persone: serve la
                # conferma dei video che l'utente ha già messo nella cartella.
                by_name = None
            chosen = by_name
            if ev and (not by_name or ev.get('id') != by_name.get('id')):
                related = fsq in _squash(ev['name']) or difflib.SequenceMatcher(
                    None, fsq, _squash(ev['name'])).ratio() >= 0.6
                if related or not by_name:
                    chosen = ev
            if chosen:
                f['performer'] = {'id': chosen.get('id'), 'name': chosen.get('name'),
                                  'gender': chosen.get('gender'), 'aliases': chosen.get('aliases') or []}
            prog.tick(n, file=f['name'])
        prog.tick(len(folders), found=sum(1 for f in folders if f['performer']), force=True)

    def build_dictionary(self):
        """Nomi riconoscibili senza rete: cartelle performer e cast già visto in libreria."""
        d = {}
        self.folder_of_perf = {}
        for f in self.lib['perfFolders'].values():
            p = f['performer'] or {'id': None, 'name': f['name'], 'gender': None, 'aliases': []}
            entry = {'id': p.get('id'), 'name': p['name'], 'gender': p.get('gender'), 'aliases': p.get('aliases'),
                     'folder': f['rel'], 'source': 'folder'}
            key = p.get('id') or _squash(p['name'])
            self.folder_of_perf.setdefault(key, []).append(f['rel'])
            single = len(f['name'].split()) == 1
            if len(_squash(f['name'])) >= 4:
                d.setdefault(_squash(f['name']), dict(entry, single=single))
            # Solo alias che sono varianti del nome ("Anna Deville"): StashDB ne elenca anche
            # di generici ("Anna Lee 2") che manderebbero nella cartella un'altra persona.
            aliases = [a for a in (p.get('aliases') or []) if len(a.split()) >= 2
                       and difflib.SequenceMatcher(None, _squash(a), _squash(p['name'])).ratio() >= 0.7]
            for n in [p['name']] + aliases:
                sq = _squash(n)
                if len(sq) >= 6:
                    d.setdefault(sq, dict(entry, single=len(n.split()) == 1))
        for it in self.items:
            for p in (it['scene'] or {}).get('performers', []):
                sq = _squash(p['name'])
                if len(p['name'].split()) >= 2 and len(sq) >= 6 and sq not in d:
                    d[sq] = {'id': p['id'], 'name': p['name'], 'gender': p.get('gender'),
                             'aliases': p.get('aliases'), 'folder': None, 'source': 'library', 'single': False}
        self.dictionary = d

    def match_dictionary(self, it):
        toks = it['parsed']['ordered']
        used = set()
        found = []
        for n in (4, 3, 2, 1):
            for i in range(len(toks) - n + 1):
                span = set(range(i, i + n))
                if span & used:
                    continue
                e = self.dictionary.get(''.join(toks[i:i + n]))
                if not e or (n == 1 and not e['single']):
                    continue
                used |= span
                found.append((e, n))
        for e, n in found:
            if e['source'] == 'folder':
                conf = 'certain' if n >= 2 else 'uncertain'
            else:
                conf = 'probable'
            q = _merge_performer(it, e, 'filename', conf)
            q['inName'] = True
        return bool(found)

    # ── durata ──
    def duration(self, it):
        if it['duration']:
            return it['duration']
        if it['oshash']:
            _, d = self.cache.vhash(it['oshash'])
            if d:
                it['duration'] = d
                return d
        d = videohash.probe_duration(it['path'], self.ffmpeg)
        it['duration'] = d
        if d and it['oshash']:
            self.cache.put_vhash(it['oshash'], duration=d)
        return d

    # ── codici scena ──
    def code_lookup(self):
        todo = [it for it in self.scope_items if not it['scene'] and it['parsed']['codes']]
        if not todo or not self.stash.enabled:
            return
        prog = Progress('code', len(todo), 'Codici scena nei nomi…')
        found = 0
        for n, it in enumerate(todo, 1):
            for code in it['parsed']['codes'][:2]:
                exact = [s for s in self.stash.search_scene(code, 5) if (s['code'] or '').upper() == code]
                if not exact:
                    continue
                blob = it['parsed']['squash']
                exact.sort(key=lambda s: -sum(1 for p in s['performers'] if _in_name(p, blob)))
                sc = exact[0]
                overlap = sum(1 for p in sc['performers'] if _in_name(p, blob))
                dur = self.duration(it)
                if dur and sc['duration'] and not _dur_ok(dur, sc['duration'], 0.10):
                    if not overlap:
                        continue
                    conf = 'probable'
                else:
                    conf = 'certain' if (overlap or len(exact) == 1) else 'probable'
                _set_scene(it, sc, 'code', conf)
                self.stats['code'] += 1
                found += 1
                break
            prog.tick(n, file=it['name'], found=found)
        self.cache.commit()
        prog.tick(len(todo), found=found, force=True)

    # ── PHASH ──
    def phash_lookup(self):
        todo = [it for it in self.scope_items if not it['scene'] and it['oshash']]
        if not todo or not self.stash.enabled or not self.use_phash:
            return
        if not self.ffmpeg:
            _warn('ffmpeg non trovato: impronta visiva saltata')
            return
        prog = Progress('phash', len(todo), 'Impronte visive (le più lente, poi restano in cache)…')
        found = 0
        pending = []

        def flush():
            nonlocal found
            if not pending:
                return
            res = self.stash.by_fingerprints('PHASH', sorted({x['phash'] for x in pending}))
            for x in pending:
                sc = res.get(x['phash'])
                if sc and not x['scene']:
                    _set_scene(x, sc, 'phash', 'certain')
                    self.stats['phash'] += 1
                    found += 1
            pending.clear()

        for n, it in enumerate(todo, 1):
            ph, dur = self.cache.vhash(it['oshash'])
            if ph is None:
                try:
                    ph, dur = videohash.phash(it['path'], self.ffmpeg, dur,
                                              workers=int(self.cfg.get('phashWorkers') or 4))
                except Exception as e:
                    ph = None
                    _warn('phash %s: %s' % (it['name'], str(e)[:120]))
                self.cache.put_vhash(it['oshash'], ph or '', dur)
                if n % 20 == 0:
                    self.cache.commit()
            it['duration'] = it['duration'] or dur
            if ph:
                it['phash'] = ph
                pending.append(it)
            if len(pending) >= 40:
                flush()
            prog.tick(n, file=it['name'], found=found)
            if not self.stash.enabled:
                break
        flush()
        self.cache.commit()
        prog.tick(len(todo), found=found, force=True)

    # ── titolo ──
    def title_lookup(self):
        todo = [it for it in self.scope_items if not it['scene'] and len(_content_tokens(it['parsed']['title'])) >= 2]
        if not todo or not self.stash.enabled or not self.use_search:
            return
        prog = Progress('title', len(todo), 'Ricerca dei titoli su StashDB…')
        found = 0
        for n, it in enumerate(todo, 1):
            title_toks = _content_tokens(it['parsed']['title'])
            blob = it['parsed']['squash']
            named = {q['key'] for q in it['performers'] if q.get('inName') and CONF_RANK[q['conf']] >= 2}
            generic = len([t for t in title_toks if t not in _VOCAB_STEMS]) <= 1
            best = None
            for sc in self.stash.search_scene(it['parsed']['title'], 6):
                scene_toks = _content_tokens(sc['title'])
                sim = _containment(title_toks, scene_toks)
                overlap = sum(1 for p in sc['performers'] if _in_name(p, blob))
                # Un titolo di due parole ("Gang Be Banging") è contenuto in troppi titoli
                # altrui: senza un performer in comune i due titoli devono quasi coincidere.
                if min(len(title_toks), len(scene_toks)) <= 2 and not overlap \
                        and len(title_toks & scene_toks) < 0.75 * max(len(title_toks), len(scene_toks)):
                    continue
                if named and not any((p.get('id') or _squash(p['name'])) in named for p in sc['performers']):
                    continue
                if sim < 0.3 and not overlap:
                    continue
                dur = self.duration(it)
                if dur:
                    if not _dur_ok(dur, sc['duration'], 0.03):
                        continue
                    if generic and not overlap:
                        # "Slutty MILF BBC Anal": un titolo di sole parole di genere somiglia
                        # a migliaia di scene, serve anche una durata quasi identica.
                        ok = sim >= 0.8 and abs(dur - sc['duration']) <= max(3.0, 0.005 * sc['duration'])
                    else:
                        ok = sim >= 0.6 or (overlap and sim >= 0.3)
                else:
                    ok = sim >= 0.9 and overlap
                if not ok:
                    continue
                score = sim + 0.5 * min(overlap, 2)
                if not best or score > best[0]:
                    tight = dur and abs(dur - sc['duration']) <= max(3.0, 0.01 * sc['duration'])
                    best = (score, sc, 'certain' if (tight and sim >= 0.9 and overlap) else 'probable')
            if best:
                _set_scene(it, best[1], 'title', best[2])
                self.stats['title'] += 1
                found += 1
            prog.tick(n, file=it['name'], found=found)
        self.cache.commit()
        prog.tick(len(todo), found=found, force=True)

    # ── nomi nel file ──
    def local_names(self, only_missing=False):
        """Nomi riconoscibili senza rete, più il performer implicito nella cartella."""
        for it in self.items:
            if only_missing and it['performers']:
                continue
            self.match_dictionary(it)
            pf = it['ctx']['perf_folder']
            if pf and pf in self.lib['perfFolders']:
                f = self.lib['perfFolders'][pf]
                p = f['performer'] or {'id': None, 'name': f['name'], 'gender': None, 'aliases': []}
                _merge_performer(it, p, 'folder', 'certain')

    def stash_names(self):
        todo = [it for it in self.scope_items if not it['scene'] and not it['performers'] and it['parsed']['windows']]
        if not todo or not self.stash.enabled or not self.use_search:
            return
        prog = Progress('names', len(todo), 'Nomi dei performer nei file…')
        found = 0
        for n, it in enumerate(todo, 1):
            for win in it['parsed']['windows'][:3]:
                exact = [p for p in self.stash.search_performer(win['text'], 5)
                         if (p.get('scene_count') or 0) >= 3 and any(_squash(x) == win['squash'] for x in _names_of(p))]
                if exact:
                    p = max(exact, key=lambda x: x.get('scene_count') or 0)
                    # "Probabile" solo col nome vero: fra gli alias di StashDB ci sono anche
                    # personaggi e nomi altrui ("Lara Croft" è un alias di Lora Craft).
                    conf = 'probable' if _squash(p['name']) == win['squash'] else 'uncertain'
                    q = _merge_performer(it, p, 'stashdb-name', conf)
                    q['inName'] = True
                    self.stats['name'] += 1
                    found += 1
                    break
            prog.tick(n, file=it['name'], found=found)
        self.cache.commit()
        prog.tick(len(todo), found=found, force=True)

    # ── doppioni ──
    def find_duplicates(self):
        """Stesso file due volte in libreria: per impronta identica, per impronta
        visiva quasi uguale, o perché puntano alla stessa scena di StashDB.
        Il "buono" è quello già smistato, o il più grande: gli altri sono copie."""
        groups = {}
        by_os = defaultdict(list)
        for it in self.items:
            if it['oshash']:
                by_os[it['oshash']].append(it)
        for h, bucket in by_os.items():
            if len(bucket) > 1:
                groups[('identico', h)] = bucket

        by_ph = defaultdict(list)
        for it in self.items:
            if it.get('phash'):
                by_ph[it['phash']].append(it)
        keys = list(by_ph)
        used = set()
        for i, k in enumerate(keys):
            if k in used:
                continue
            bucket = list(by_ph[k])
            used.add(k)
            for k2 in keys[i + 1:]:
                if k2 not in used and videohash.hamming(k, k2) <= 4:
                    bucket.extend(by_ph[k2])
                    used.add(k2)
            if len(bucket) > 1:
                groups[('stesso video', k)] = bucket

        by_scene = defaultdict(list)
        for it in self.items:
            sc = it['scene']
            # Solo riconoscimenti forti: due titoli uguali non bastano a dire "doppione".
            if sc and sc.get('id') and it['sceneSource'] in ('oshash', 'phash', 'code'):
                by_scene[sc['id']].append(it)
        for sid, bucket in by_scene.items():
            if len(bucket) > 1:
                groups[('stessa scena', sid)] = bucket

        rank = {'identico': 3, 'stesso video': 2, 'stessa scena': 1}
        for (kind, _), bucket in groups.items():
            keeper = max(bucket, key=lambda it: (0 if it['ctx']['unsorted'] else 1,
                                                 it['size'], it['duration'] or 0))
            for it in bucket:
                if it is keeper:
                    continue
                cur = it.get('dup')
                if cur and rank[cur['kind']] >= rank[kind]:
                    continue
                it['dup'] = {'kind': kind, 'of': keeper['rel'], 'ofName': keeper['name']}
        self.stats['duplicates'] = sum(1 for it in self.scope_items if it.get('dup'))

    # ── tipologia ──
    def learn_categories(self):
        cats = self.lib['categories']
        train = [it for it in self.items if it['ctx']['category'] in cats]
        self.cat_names = sorted(cats)
        self.clf, self.calib = None, None
        if len(train) < 20 or len({it['ctx']['category'] for it in train}) < 2:
            return
        _emit({'type': 'phase', 'phase': 'learn', 'text': 'Imparo le tue tipologie da %d file già smistati…' % len(train)})
        X = [features(it) for it in train]
        y = [it['ctx']['category'] for it in train]
        self.calib = calibrate(X, y)
        self.clf = Classifier().fit(X, y)

    # ── tipologia dalle immagini ──
    def visual(self):
        """Quando il nome non dice niente ("68c4eb094c99.mp4") il testo tace, le
        immagini no: qualche fotogramma passa dentro CLIP e un classificatore
        addestrato sulle TUE cartelle dice di che tipologia sembra. Parla solo
        sopra la soglia calibrata, altrimenti sbaglierebbe più di quanto aiuta."""
        self.vis = None
        if not self.cfg.get('useVisual'):
            return
        targets = [it for it in self.scope_items
                   if not it['ctx']['category'] and not it.get('dup') and it['oshash']]
        if not targets:
            return
        cats = self.lib['categories']
        pool = defaultdict(list)
        for it in self.items:
            if it['ctx']['category'] in cats and it['oshash'] and not it.get('dup'):
                pool[it['ctx']['category']].append(it)
        cap = max(20, int(self.cfg.get('visualMaxPerClass') or 120))
        rnd = random.Random(11)
        train = []
        for cat in sorted(pool):
            lst = sorted(pool[cat], key=lambda x: x['rel'])
            if len(lst) < 12:      # con pochi esempi la classe non si impara
                continue
            train.extend(lst if len(lst) <= cap else rnd.sample(lst, cap))
        if len(train) < 40 or len({it['ctx']['category'] for it in train}) < 2:
            _warn('immagini: troppi pochi file già smistati per imparare le tipologie')
            return
        try:
            import numpy as np, torch, visualtag  # noqa: F401
        except ImportError as e:
            _warn('analisi delle immagini non disponibile: %s' % e)
            return
        if not self.ffmpeg:
            _warn('analisi delle immagini: ffmpeg non trovato')
            return
        try:
            if not visualtag.have_model():
                if not self.cfg.get('visualDownload', True):
                    _warn('immagini: modello assente e scaricamento disattivato')
                    return
                pm = Progress('visual', 100, 'Scarico il modello immagini (335 MB, una volta sola)…')
                visualtag.ensure_model(lambda d, t: pm.tick(int(100.0 * d / t) if t else 0))
                pm.tick(100, force=True)
        except Exception as e:
            _warn('modello immagini non scaricato: %s' % str(e)[:160])
            return

        seen = {it['id'] for it in train}
        todo = train + [it for it in targets if it['id'] not in seen]
        prog = Progress('visual', len(todo),
                        'Guardo i fotogrammi di %d video (una volta sola)…' % len(todo))
        vecs, fails = {}, 0
        for k, it in enumerate(todo, 1):
            blob = self.cache.clipemb(it['oshash'])
            if blob is None:
                try:
                    emb = visualtag.embed(it['path'], self.ffmpeg, self.duration(it),
                                          int(self.cfg.get('visualFrames') or 8))
                except Exception:
                    emb = None
                blob = visualtag.pack(emb) if emb is not None else b''
                self.cache.put_clipemb(it['oshash'], blob)
                if k % 20 == 0:
                    self.cache.commit()
            if blob:
                vecs[it['id']] = visualtag.unpack(blob)
            else:
                fails += 1
            prog.tick(k, file=it['name'])
        self.cache.commit()
        prog.tick(len(todo), force=True)

        tr = [it for it in train if it['id'] in vecs]
        labels = sorted({it['ctx']['category'] for it in tr})
        if len(tr) < 40 or len(labels) < 2:
            _warn('immagini: fotogrammi illeggibili su troppi file (%d)' % fails)
            return
        _emit({'type': 'phase', 'phase': 'visual',
               'text': 'Imparo le tipologie dalle immagini di %d file…' % len(tr)})
        X = np.stack([vecs[it['id']] for it in tr]).astype(np.float32)
        y = np.array([labels.index(it['ctx']['category']) for it in tr])
        try:
            m = train_visual(X, y, labels)
        except Exception as e:
            _warn('addestramento sulle immagini fallito: %s' % str(e)[:160])
            return
        t90, t70 = m['t90'], m['t70']
        used = 0
        for it in targets:
            v = vecs.get(it['id'])
            if v is None:
                continue
            with torch.no_grad():
                row = torch.tensor(np.asarray([v], dtype=np.float32)) @ m['W'] + m['B']
                p = torch.softmax(row, 1).numpy()[0]
            j = int(p.argmax())
            conf = 'probable' if (t90 is not None and p[j] >= t90) else (
                'uncertain' if (t70 is not None and p[j] >= t70) else 'none')
            it['visual'] = {'name': labels[j], 'p': float(p[j]), 'conf': conf}
            if conf != 'none':
                used += 1
        self.vis = {'trained': len(tr), 'classes': len(labels), 'embedded': len(vecs),
                    'unreadable': fails, 'accuracy': m['accuracy'], 'coverage90': m['coverage90'],
                    't90': t90, 't70': t70, 'used': used}
        _emit({'type': 'phase', 'phase': 'visual',
               'text': 'Immagini: %d%% di risposte esatte in prova, usate su %d file' % (
                   round(100 * m['accuracy']), used)})

    def predict_category(self, it):
        if it['ctx']['category']:
            return {'name': it['ctx']['category'], 'conf': 'certain', 'source': 'folder'}
        vis = it.get('visual') or {}
        if vis.get('conf') in (None, 'none'):
            vis = {}
        text = None
        if self.clf:
            lab, margin, nf = self.clf.predict(features(it))
            c = self.calib or {}
            if lab is not None and nf >= 1:
                # Un solo indizio non basta per "probabile", qualunque sia il margine.
                if c.get('t90') is not None and margin >= c['t90'] and nf >= 2:
                    text = {'name': lab, 'conf': 'probable', 'source': 'learned', 'margin': round(margin, 3)}
                elif c.get('t70') is not None and margin >= c['t70']:
                    text = {'name': lab, 'conf': 'uncertain', 'source': 'learned', 'margin': round(margin, 3)}
        if text and vis:
            if vis['name'] == text['name']:
                # Nome e immagini d'accordo: due indizi indipendenti, sale di livello.
                return dict(text, conf='probable', source='learned+visual', visual=round(vis['p'], 3))
            # In disaccordo vince il nome, ma non si può più chiamare probabile.
            return dict(text, conf='uncertain', visual=round(vis['p'], 3))
        if text:
            return text
        if vis:
            return {'name': vis['name'], 'conf': vis['conf'], 'source': 'visual',
                    'visual': round(vis['p'], 3)}
        lit = literal_category(it, self.cat_names)
        if lit:
            return {'name': lit, 'conf': 'uncertain', 'source': 'name'}
        return None

    # ── piano ──
    def _perf_folders_for(self, p):
        rels = list(self.folder_of_perf.get(p['key'], []))
        if not rels:
            for sq in {_squash(n) for n in _names_of(p)}:
                e = self.dictionary.get(sq)
                if e and e.get('folder') and len(sq) >= 6 and not e.get('single'):
                    rels.append(e['folder'])
        return sorted(set(rels))

    def _primary(self, it, category):
        best, best_rank, alts = None, -999, []
        for p in it['performers']:
            male = (p.get('gender') or '') in MALE_GENDERS
            folders = self._perf_folders_for(p)
            # Un video già nella cartella di uno dei suoi performer ci resta: spostarlo
            # nella cartella di un altro membro del cast sarebbe solo rimescolare.
            here = bool(it['ctx']['perf_folder']) and it['ctx']['perf_folder'] in folders
            rank = (100 if folders else 0) + (30 if here else 0) + (20 if p.get('inName') else 0) \
                - (50 if male else 0) + CONF_RANK[p['conf']] * 2
            if folders:
                alts.append(p['name'])
            if male and not folders:
                continue
            if rank > best_rank:
                best, best_rank = p, rank
        if best is None:
            return None, None, []
        folders = self._perf_folders_for(best)
        if category:
            in_cat = [f for f in folders if f.split(os.sep)[0] == category['name']]
            folders = in_cat or folders
        return best, (folders[0] if folders else None), [a for a in alts if a != best['name']]

    def _finalize(self, it, cat, primary, dest_dir, conf, reason, new_folder, taken):
        """Trasforma la decisione in percorso reale: controlla che non sia già al
        posto giusto e che non ci sia già un file identico a destinazione."""
        dest = None
        if dest_dir and self.layout != 'tags_only':
            abs_dir = os.path.join(self.root, dest_dir)
            if _inside(it['path'], abs_dir) and (not it['ctx']['unsorted'] or os.path.dirname(it['path']) == abs_dir):
                reason = 'Già al posto giusto'
                dest_dir, conf = None, 'none'
            else:
                dest, same = _unique_dest(abs_dir, it['name'], taken, it)
                if same:
                    reason = 'Già presente in %s' % dest_dir
                    dest, dest_dir, conf = None, None, 'none'
        if not dest and not reason:
            reason = 'Non riconosciuto'
        if not dest and self.layout != 'tags_only':
            conf = 'none'
        if self.layout == 'tags_only':
            conf = max([(primary or {}).get('conf', 'none'), (cat or {}).get('conf', 'none')],
                       key=lambda c: CONF_RANK[c])
            reason = reason or 'Solo tag'
        return _item_out(it, cat, primary, dest, dest_dir, conf, reason, new_folder)

    def plan(self):
        _emit({'type': 'phase', 'phase': 'plan', 'text': 'Preparo le proposte…'})
        cats = self.lib['categories']
        containers = sorted((d for d, r in self.lib['roles'].items() if r == ROLE_PERFORMERS),
                            key=lambda d: -self.lib['counts'].get(d, 0))
        container = self.cfg.get('performerContainer')
        if container not in containers:
            container = containers[0] if containers else None
        unknown_dir = (self.cfg.get('unknownFolder') or '_Senza performer').strip() or '_Senza performer'

        decisions = []
        per_perf = Counter()
        perf_cats = defaultdict(Counter)
        for it in self.scope_items:
            cat = self.predict_category(it)
            primary, folder, alts = self._primary(it, cat)
            decisions.append((it, cat, primary, folder, alts))
            # Le cartelle nuove nascono solo da riconoscimenti almeno probabili.
            if primary and not folder and CONF_RANK[primary['conf']] >= 2:
                per_perf[primary['key']] += 1
                if cat and cats[cat['name']]['role'] == ROLE_CATEGORY_PERFORMERS and CONF_RANK[cat['conf']] >= 2:
                    perf_cats[primary['key']][cat['name']] += 1
        # Una sola cartella nuova per performer: nella tipologia-con-performer solo se
        # ci finisce la maggioranza dei suoi video, altrimenti nel contenitore scelto.
        new_base = {}
        for key, n in per_perf.items():
            top = perf_cats[key].most_common(1)
            new_base[key] = top[0][0] if top and top[0][1] * 2 > n else container
        # Nelle tipologie divise per performer, i video senza performer aspettano nella
        # sottocartella "da smistare" già usata dall'utente (es. DILDOSTAR\DA ORDINARE).
        inbox = {}
        for name, c in cats.items():
            if c['role'] != ROLE_CATEGORY_PERFORMERS:
                continue
            kids = [d for d in self.lib['tree'].get(name, {}).get('dirs', [])
                    if _is_unsorted_name(d, self.lib['unsortedNames'])]
            if kids:
                inbox[name] = os.path.join(name, max(
                    kids, key=lambda d: self.lib['counts'].get(os.path.join(name, d), 0)))

        taken = set()
        out = []
        for it, cat, primary, folder, alts in decisions:
            dest_dir, conf, reason, new_folder = None, 'none', '', False
            pconf = primary['conf'] if primary else 'none'
            big_enough = bool(primary) and self.cfg.get('createPerformerFolders', True) \
                and per_perf[primary['key']] >= self.min_perf
            cat_conf = cat['conf'] if cat else 'none'
            cat_rel = cat['name'] if cat else None

            # Un doppione non va organizzato come se fosse un video a sé: o resta
            # dov'è, o finisce nella cartella che l'utente ha scelto per le copie.
            if it.get('dup'):
                dup_dir = (self.cfg.get('duplicatesFolder') or '').strip().strip('\\/')
                reason = 'Doppione (%s) di %s' % (it['dup']['kind'], it['dup']['ofName'])
                if dup_dir:
                    dest_dir = dup_dir
                    conf = 'certain' if it['dup']['kind'] == 'identico' else 'probable'
                out.append(self._finalize(it, cat, primary, dest_dir, conf, reason, False, taken))
                continue

            if self.layout == 'current':
                base = new_base.get(primary['key']) if big_enough and not folder else None
                if folder:
                    dest_dir, conf = folder, pconf
                    reason = '%s ha già una cartella' % primary['name']
                    if alts and not primary.get('inName'):
                        conf = min(conf, 'probable', key=lambda c: CONF_RANK[c])
                        reason += ' (in scena anche: %s)' % ', '.join(alts[:2])
                elif base:
                    dest_dir, conf = os.path.join(base, _safe_dirname(primary['name'])), pconf
                    new_folder = not os.path.isdir(os.path.join(self.root, dest_dir))
                    reason = '%s: %d video da smistare' % (primary['name'], per_perf[primary['key']])
                elif cat and cats[cat_rel]['role'] == ROLE_CATEGORY_PERFORMERS:
                    if it['ctx']['category'] == cat_rel and it['ctx']['unsorted']:
                        reason = '%s: manca il performer, resta da smistare' % cat_rel
                    else:
                        dest_dir, conf = inbox.get(cat_rel, cat_rel), cat_conf
                        reason = _cat_reason(cat) + ' · performer da assegnare'
                elif cat:
                    dest_dir, conf = cat_rel, cat_conf
                    reason = _cat_reason(cat)
                elif primary and CONF_RANK[pconf] < 2:
                    reason = '%s: nome da verificare' % primary['name']
                elif primary:
                    reason = '%s: %d video da smistare, ne servono %d per creare la cartella' % (
                        primary['name'], per_perf[primary['key']], self.min_perf)
            elif self.layout == 'category_performer':
                if cat:
                    dest_dir, conf, reason = cat_rel, cat_conf, _cat_reason(cat)
                    if primary and (big_enough or folder):
                        dest_dir = os.path.join(cat_rel, _safe_dirname(primary['name']))
                        conf = min(cat_conf, pconf, key=lambda c: CONF_RANK[c])
                        reason += ' · %s' % primary['name']
                        new_folder = not os.path.isdir(os.path.join(self.root, dest_dir))
            elif self.layout == 'performer_category':
                if primary and (big_enough or folder):
                    dest_dir = os.path.join(_safe_dirname(primary['name']), cat_rel) if cat else _safe_dirname(primary['name'])
                    conf = min(pconf, cat_conf, key=lambda c: CONF_RANK[c]) if cat else pconf
                    reason = primary['name'] + (' · ' + _cat_reason(cat) if cat else '')
                    new_folder = not os.path.isdir(os.path.join(self.root, dest_dir))
                elif cat:
                    dest_dir, conf = os.path.join(unknown_dir, cat_rel), cat_conf
                    reason = 'Performer non riconosciuto · ' + _cat_reason(cat)

            out.append(self._finalize(it, cat, primary, dest_dir, conf, reason, new_folder, taken))
        return out

    def run(self):
        self.scan()
        self.hash_files()
        self.fingerprint_lookup()
        self.map_performer_folders()
        self.build_dictionary()
        self.local_names()
        self.code_lookup()
        self.phash_lookup()
        self.title_lookup()
        # Il cast scoperto con codici, impronte visive e titoli rende riconoscibili
        # per nome anche gli altri file degli stessi performer.
        self.build_dictionary()
        self.local_names(only_missing=True)
        self.stash_names()
        self.find_duplicates()
        self.learn_categories()
        self.visual()
        items = self.plan()
        moves = Counter(i['conf'] for i in items if i['dest'])
        summary = {
            'root': self.root, 'layout': self.layout, 'scope': self.scope,
            'scanned': len(self.items), 'inScope': len(self.scope_items),
            'identified': {k: self.stats[k] for k in ('oshash', 'code', 'phash', 'title', 'name')},
            'withPerformer': sum(1 for i in items if i['primary']),
            'withCategory': sum(1 for i in items if i['category']),
            'duplicates': sum(1 for i in items if i.get('dup')),
            'moves': {k: moves.get(k, 0) for k in ('certain', 'probable', 'uncertain')},
            'unchanged': sum(1 for i in items if not i['dest']),
            'newFolders': sorted({i['destRel'] for i in items if i['dest'] and i['newFolder']}),
            'classifier': self.calib,
            'visual': self.vis,
            'stash': {'enabled': self.stash.enabled, 'requests': self.stash.requests,
                      'disabled': self.stash.disabled_reason},
            'elapsed': round(time.time() - self.t0, 1),
        }
        targets = {
            'root': self.root,
            'categories': [{'name': c, 'rel': c} for c in self.cat_names],
            'performerFolders': sorted(({'name': f['name'], 'rel': f['rel'], 'container': f['container'],
                                         'performer': (f['performer'] or {}).get('name')}
                                        for f in self.lib['perfFolders'].values()), key=lambda f: f['rel'].lower()),
        }
        _emit({'type': 'done', 'ok': True, 'summary': summary, 'items': items, 'targets': targets})


def _cat_reason(cat):
    return {'folder': 'Tipologia della cartella: %s', 'learned': 'Tipologia imparata dai tuoi file: %s',
            'name': 'Tipologia dal nome: %s', 'visual': 'Tipologia dalle immagini: %s',
            'learned+visual': 'Tipologia da nome e immagini: %s'}.get(cat['source'], '%s') % cat['name']


def _safe_dirname(name):
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', ' ', name or '').strip().rstrip('.')
    return re.sub(r'\s+', ' ', n) or 'Sconosciuto'


def _inside(path, directory):
    p, d = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(directory))
    return p.startswith(d.rstrip(os.sep) + os.sep)


def _unique_dest(abs_dir, name, taken, it):
    base, ext = os.path.splitext(name)
    cand = os.path.join(abs_dir, name)
    n = 2
    while True:
        key = os.path.normcase(cand)
        if key not in taken:
            if not os.path.exists(cand):
                break
            try:
                if os.path.getsize(cand) == it['size'] and it['oshash'] and videohash.oshash(cand) == it['oshash']:
                    return None, True
            except OSError:
                pass
        cand = os.path.join(abs_dir, '%s (%d)%s' % (base, n, ext))
        n += 1
    taken.add(os.path.normcase(cand))
    return cand, False


def _item_out(it, cat, primary, dest, dest_dir, conf, reason, new_folder):
    sc = it['scene']
    perfs = sorted(it['performers'], key=lambda p: ((p.get('gender') or '') in MALE_GENDERS, not p.get('inName'),
                                                     -CONF_RANK[p['conf']]))
    return {
        'id': it['id'], 'path': it['path'], 'rel': it['rel'], 'name': it['name'], 'size': it['size'],
        'duration': it['duration'] or (sc or {}).get('duration') or None,
        'scene': {'title': sc['title'], 'studio': sc['studio'], 'code': sc['code'], 'date': sc['date'],
                  'source': it['sceneSource'], 'conf': it['sceneConf']} if sc else None,
        'performers': [{'id': p.get('id'), 'name': p['name'], 'gender': p.get('gender'), 'source': p['source'],
                        'conf': p['conf'], 'inName': bool(p.get('inName'))} for p in perfs[:8]],
        'primary': primary['name'] if primary else None,
        'category': cat,
        'tags': (sc or {}).get('tags', [])[:14],
        'dest': dest, 'destRel': dest_dir, 'newFolder': bool(new_folder and dest),
        'conf': conf, 'reason': reason, 'dup': it.get('dup'),
    }


def cmd_analyze(args):
    with open(args.config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if not cfg.get('root') or not os.path.isdir(cfg['root']):
        _emit({'type': 'error', 'error': 'cartella libreria non valida'})
        return 1
    Analyzer(cfg, args.cache, args.ffmpeg).run()
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('detect')
    d.add_argument('--root', required=True)
    d.add_argument('--roles', default=None)
    a = sub.add_parser('analyze')
    a.add_argument('--config', required=True)
    a.add_argument('--cache', required=True)
    a.add_argument('--ffmpeg', default=None)
    args = ap.parse_args()
    try:
        return cmd_detect(args) if args.cmd == 'detect' else cmd_analyze(args)
    except Exception as e:
        _emit({'type': 'error', 'ok': False, 'error': str(e)[:300], 'trace': traceback.format_exc()[-1500:]})
        return 0 if args.cmd == 'detect' else 1


if __name__ == '__main__':
    sys.exit(main())
