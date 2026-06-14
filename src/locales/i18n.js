/* Maniac i18n — runtime mini, no build step.
 * Funziona inline nel renderer monolitico.
 *
 * API:
 *   await i18n.init();          // legge locale persistito + carica tutte le lingue
 *   i18n.t('key.path', vars)    // restituisce la stringa nella lingua corrente
 *   await i18n.set('en')        // cambia lingua + salva + emette evento
 *   i18n.locale                 // codice lingua corrente (getter)
 *   i18n.languages              // ['it','en','es','de','zh','ja']
 *   i18n.onChange(cb)           // sub: cb(newCode) ad ogni cambio
 *
 * Pattern di interpolazione: "Ciao {{name}}" + t('greet', {name: 'X'})
 * Fallback: chiave non trovata → fallback en → key letterale.
 */
(function (global) {
  const LANGS = ['it', 'en', 'es', 'de', 'zh', 'ja'];
  const FALLBACK = 'en';
  const state = {
    locale: 'it',
    bundles: {},
    listeners: new Set(),
  };

  function _resolve(obj, keyPath) {
    if (!obj) return undefined;
    const parts = keyPath.split('.');
    let cur = obj;
    for (const p of parts) {
      if (cur && typeof cur === 'object' && p in cur) cur = cur[p];
      else return undefined;
    }
    return typeof cur === 'string' ? cur : undefined;
  }

  function _interp(s, vars) {
    if (!vars) return s;
    return s.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) =>
      (vars[k] !== undefined && vars[k] !== null) ? String(vars[k]) : '');
  }

  async function _loadBundle(code) {
    if (state.bundles[code]) return state.bundles[code];
    try {
      const res = await fetch('locales/' + code + '/translation.json');
      if (!res.ok) throw new Error('http ' + res.status);
      state.bundles[code] = await res.json();
    } catch (e) {
      console.warn('[i18n] bundle ' + code + ' load fail:', e.message);
      state.bundles[code] = {};
    }
    return state.bundles[code];
  }

  const i18n = {
    languages: LANGS,
    get locale() { return state.locale; },

    async init() {
      // 1. tenta locale persistito via IPC
      let initial = 'it';
      try {
        if (global.maniac && global.maniac.i18n && global.maniac.i18n.getLocale) {
          const v = await global.maniac.i18n.getLocale();
          if (typeof v === 'string' && LANGS.includes(v)) initial = v;
        }
      } catch (_) {}
      // 2. fallback navigator.language
      if (!initial) {
        const nav = (global.navigator && global.navigator.language || 'en')
          .toLowerCase().slice(0, 2);
        initial = LANGS.includes(nav) ? nav : 'it';
      }
      state.locale = initial;
      // Pre-carica bundle corrente + fallback
      await _loadBundle(initial);
      if (initial !== FALLBACK) await _loadBundle(FALLBACK);
      try { document.documentElement.lang = initial; } catch (_) {}
      return initial;
    },

    t(key, vars) {
      const cur = state.bundles[state.locale];
      let s = _resolve(cur, key);
      if (s === undefined) {
        const fb = state.bundles[FALLBACK];
        s = _resolve(fb, key);
      }
      if (s === undefined) s = key;
      return _interp(s, vars);
    },

    async set(code) {
      if (!LANGS.includes(code)) return false;
      await _loadBundle(code);
      state.locale = code;
      try { document.documentElement.lang = code; } catch (_) {}
      try {
        if (global.maniac && global.maniac.i18n && global.maniac.i18n.setLocale)
          await global.maniac.i18n.setLocale(code);
      } catch (_) {}
      state.listeners.forEach(cb => { try { cb(code); } catch (_) {} });
      // Applica testi a tutti gli elementi con data-i18n="key" presenti nel DOM
      try {
        document.querySelectorAll('[data-i18n]').forEach(el => {
          const k = el.getAttribute('data-i18n');
          if (k) el.textContent = i18n.t(k);
        });
        document.querySelectorAll('[data-i18n-attr]').forEach(el => {
          // formato "title:key.path,placeholder:other.key"
          const spec = el.getAttribute('data-i18n-attr') || '';
          spec.split(',').forEach(pair => {
            const [attr, k] = pair.split(':').map(x => x && x.trim());
            if (attr && k) el.setAttribute(attr, i18n.t(k));
          });
        });
      } catch (_) {}
      return true;
    },

    onChange(cb) {
      if (typeof cb === 'function') state.listeners.add(cb);
      return () => state.listeners.delete(cb);
    },

    // Utility per il selettore lingua: ritorna meta { code, name, flag }
    meta() {
      return [
        { code: 'it', name: 'Italiano', flag: '🇮🇹' },
        { code: 'en', name: 'English',  flag: '🇬🇧' },
        { code: 'es', name: 'Español',  flag: '🇪🇸' },
        { code: 'de', name: 'Deutsch',  flag: '🇩🇪' },
        { code: 'zh', name: '中文',      flag: '🇨🇳' },
        { code: 'ja', name: '日本語',    flag: '🇯🇵' },
      ];
    },
  };

  global.i18n = i18n;
})(typeof window !== 'undefined' ? window : globalThis);
