/*
 * Cartes Lovelace Sentinel — style iOS moderne : surfaces translucides, grands arrondis, chiffres arrondis,
 * couleurs réservées aux niveaux. Toutes les couleurs viennent du thème Home Assistant (thème iOS inclus).
 *
 *   type: custom:sentinel-status-card    statut global (options : show_counts, show_modes)
 *   type: custom:sentinel-control-card   statut + petits boutons pour activer les modes
 *   type: custom:sentinel-mode-card      changement de mode (avec digicode si le mode l'exige)
 *   type: custom:sentinel-keypad-card    digicode d'armement / désarmement
 *   type: custom:sentinel-zones-card     zones et leur niveau
 *   type: custom:sentinel-ack-card       éléments à traiter, acquittement
 *   type: custom:sentinel-chips-card     chips : mode, statut, zones, acquitter (chips: [mode, status, zones, ack])
 *   type: custom:sentinel-plan-card      plan 2D et maquette 3D déplaçable
 *   type: custom:sentinel-journal-card   journal des évènements
 *
 * Les entités sont retrouvées toutes seules grâce à l'attribut `sentinel_key`.
 */
(function () {
  "use strict";
  if (window.__sentinelCards) return;
  window.__sentinelCards = true;

  var LV_LABEL = { critical: "Critical", warning: "Warning", info: "Info", ok: "Calme" };
  var ICON = {
    ok: "mdi:shield-check", info: "mdi:shield-half-full", warning: "mdi:shield-alert-outline", critical: "mdi:shield-alert",
    des: "mdi:lock-open-variant", pre: "mdi:home", abs: "mdi:lock", nui: "mdi:weather-night"
  };
  var RANK = { critical: 3, warning: 2, info: 1, ok: 0 };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function colorOf(level) {
    return { critical: "var(--s-red)", warning: "var(--s-orange)", info: "var(--s-blue)", ok: "var(--s-green)" }[level] || "var(--s-sec)";
  }
  function find(hass, key) {
    var st = hass.states;
    for (var id in st) { var a = st[id].attributes; if (a && a.sentinel_key === key) return st[id]; }
    return null;
  }
  function zoneStates(hass) {
    var out = [], st = hass.states;
    for (var id in st) {
      var a = st[id].attributes;
      if (a && typeof a.sentinel_key === "string" && a.sentinel_key.indexOf("zone:") === 0) out.push(st[id]);
    }
    return out;
  }
  function secondsLeft(iso) {
    if (!iso) return 0;
    return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 1000));
  }
  function modeIcon(m, idx) {
    return ICON[m.id] || ICON[["des", "pre", "abs", "nui"][idx]] || "mdi:shield-home";
  }
  function moreInfo(el, entityId) {
    el.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: entityId }, bubbles: true, composed: true }));
  }
  function plural(n, one, many) { return n + " " + (n > 1 ? many : one); }

  var CSS = [
    ":host{display:block;--s-blue:var(--primary-color,#007aff);--s-red:var(--error-color,#ff3b30);--s-orange:var(--warning-color,#ff9500);",
    "--s-green:var(--success-color,#34c759);--s-sec:var(--secondary-text-color,#8e8e93);--s-txt:var(--primary-text-color,#000);",
    "--s-fill:color-mix(in srgb,var(--s-sec) 13%,transparent);--s-fill2:color-mix(in srgb,var(--s-sec) 22%,transparent);",
    "--s-sep:var(--divider-color,rgba(60,60,67,.18));--s-card:var(--ha-card-background,var(--card-background-color,#fff));",
    "--s-num:ui-rounded,'SF Pro Rounded',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;",
    "font-family:var(--paper-font-body1_-_font-family,-apple-system,BlinkMacSystemFont,'SF Pro Text','Helvetica Neue',system-ui,sans-serif);",
    "color:var(--s-txt);-webkit-font-smoothing:antialiased}",
    "ha-card{padding:18px;overflow:hidden;display:block;border-radius:var(--ha-card-border-radius,20px)}",
    "ha-card.tint{background:linear-gradient(145deg,color-mix(in srgb,var(--c) 16%,var(--s-card)),var(--s-card) 62%)}",
    "ha-card.bare{background:transparent;box-shadow:none;border:0;padding:0;overflow:visible}",
    "*{box-sizing:border-box}",
    "button{font:inherit;color:inherit;background:none;border:0;padding:0;cursor:pointer;-webkit-tap-highlight-color:transparent;text-align:inherit;transition:transform .12s ease,background .15s ease,opacity .15s ease}",
    "button:active{transform:scale(.97)}",
    ".row{display:flex;align-items:center;gap:16px}",
    ".sp{flex:1;min-width:0}",
    ".cap{font-size:13px;letter-spacing:.03em;text-transform:uppercase;color:var(--s-sec);margin:0 0 12px 2px;font-weight:600;display:flex;justify-content:space-between;align-items:center;min-height:22px}",
    ".cap .r{text-transform:none;letter-spacing:0;font-size:14px;font-weight:500}",
    ".t{font-size:17px;font-weight:600;line-height:1.25;letter-spacing:-.01em}",
    ".t.big{font-family:var(--s-num);font-size:28px;letter-spacing:-.03em;font-weight:700;line-height:1.08}",
    ".s{font-size:14px;color:var(--s-sec);line-height:1.35}",
    ".s.c{color:var(--c);font-weight:500}",
    /* statut */
    ".ring{position:relative;width:64px;height:64px;flex:none;display:grid;place-items:center;color:var(--c)}",
    ".ring::before{content:'';position:absolute;inset:0;border-radius:50%;background:color-mix(in srgb,var(--c) 18%,transparent)}",
    ".ring.live::after{content:'';position:absolute;inset:0;border-radius:50%;border:2px solid var(--c);animation:pulse 2s ease-out infinite}",
    "@keyframes pulse{0%{transform:scale(.85);opacity:.9}100%{transform:scale(1.35);opacity:0}}",
    ".ring ha-icon{--mdc-icon-size:30px;position:relative}",
    ".stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:18px}",
    ".stat{background:var(--s-fill);border-radius:14px;padding:10px 12px}",
    ".stat b{display:block;font-family:var(--s-num);font-size:28px;font-weight:700;letter-spacing:-.03em;line-height:1.05;font-variant-numeric:tabular-nums}",
    ".stat span{font-size:12.5px;color:var(--s-sec);font-weight:500}",
    ".stat.hit{background:color-mix(in srgb,var(--c) 18%,transparent)}",
    ".stat.hit b,.stat.hit span{color:var(--c)}",
    /* modes */
    ".seg{display:flex;background:var(--s-fill);border-radius:12px;padding:3px;gap:2px}",
    ".seg button{flex:1;min-width:0;padding:8px 4px;border-radius:9px;font-size:13.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center}",
    ".seg button.on{background:var(--s-card);box-shadow:0 2px 8px rgba(0,0,0,.14),0 0 0 .5px var(--s-sep);font-weight:600}",
    ".modes{display:flex;gap:6px;flex-wrap:wrap}",
    ".mbtn{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 13px 0 10px;border-radius:17px;background:var(--s-fill);font-size:13.5px;font-weight:500;white-space:nowrap;min-width:0}",
    ".mbtn ha-icon{--mdc-icon-size:16px;color:var(--s-sec);flex:none}",
    ".mbtn.on{background:color-mix(in srgb,var(--c) 17%,transparent);color:var(--c);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--c) 50%,transparent);font-weight:600}",
    ".mbtn.on ha-icon{color:var(--c)}",
    ".modes.tight{margin-top:14px}",
    /* listes en tuiles */
    ".tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}",
    ".tile{display:flex;align-items:center;gap:10px;padding:12px;border-radius:16px;background:var(--s-fill);min-width:0;width:100%}",
    ".tile .ic{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;flex:none;background:color-mix(in srgb,var(--c) 20%,transparent);color:var(--c)}",
    ".tile .ic ha-icon{--mdc-icon-size:19px}",
    ".tile .tx{min-width:0}",
    ".tile .tx b{display:block;font-size:15px;font-weight:600;letter-spacing:-.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".tile .tx span{font-size:12.5px;color:var(--s-sec);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".tile.hot{background:color-mix(in srgb,var(--c) 15%,var(--s-card));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--c) 45%,transparent)}",
    ".tile.hot .tx span{color:var(--c);font-weight:600}",
    ".more{display:block;width:100%;margin-top:10px;padding:10px;border-radius:12px;background:var(--s-fill);color:var(--s-blue);font-size:15px;font-weight:500;text-align:center}",
    /* à traiter */
    ".stack{display:flex;flex-direction:column;gap:8px}",
    ".item{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:16px;background:var(--s-fill)}",
    ".item .ic{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;flex:none;background:color-mix(in srgb,var(--c) 20%,transparent);color:var(--c)}",
    ".item .ic ha-icon{--mdc-icon-size:20px}",
    ".item .tx{flex:1;min-width:0}",
    ".item .tx b{display:block;font-size:15.5px;font-weight:600;letter-spacing:-.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".item .tx span{font-size:13px;color:var(--s-sec);display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".item.acked{opacity:.6}",
    ".pillbtn{background:var(--s-blue);color:#fff;font-size:14px;font-weight:600;padding:7px 14px;border-radius:15px;flex:none}",
    ".pillbtn.soft{background:color-mix(in srgb,var(--s-blue) 15%,transparent);color:var(--s-blue)}",
    ".empty{display:flex;align-items:center;justify-content:center;gap:8px;color:var(--s-sec);font-size:15px;padding:10px 0 4px}",
    ".empty ha-icon{--mdc-icon-size:22px;color:var(--s-green)}",
    ".count{font-family:var(--s-num);font-size:13px;font-weight:600;font-variant-numeric:tabular-nums;color:#fff;background:var(--s-red);min-width:22px;height:22px;padding:0 7px;border-radius:11px;display:inline-grid;place-items:center}",
    /* chips */
    ".chips{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;padding:2px 2px 6px}",
    ".chips::-webkit-scrollbar{display:none}",
    ".chip{display:inline-flex;align-items:center;gap:8px;height:38px;padding:0 14px 0 5px;border-radius:19px;background:color-mix(in srgb,var(--s-card) 78%,transparent);",
    "-webkit-backdrop-filter:blur(18px) saturate(170%);backdrop-filter:blur(18px) saturate(170%);box-shadow:0 0 0 .5px var(--s-sep),0 2px 10px rgba(0,0,0,.06);font-size:14.5px;font-weight:600;letter-spacing:-.01em;white-space:nowrap;flex:none}",
    ".chip .ic{width:28px;height:28px;border-radius:50%;display:grid;place-items:center;background:color-mix(in srgb,var(--c,var(--s-sec)) 20%,transparent);color:var(--c,var(--s-sec));flex:none}",
    ".chip .ic ha-icon{--mdc-icon-size:16px}",
    ".chip.solid{background:var(--c);color:#fff;box-shadow:0 4px 14px color-mix(in srgb,var(--c) 40%,transparent)}",
    ".chip.solid .ic{background:rgba(255,255,255,.26);color:#fff}",
    /* digicode */
    ".dots{display:flex;justify-content:center;gap:8px;margin:14px 0 6px}",
    ".dots i{width:34px;height:42px;border-radius:10px;background:var(--s-fill);display:grid;place-items:center;transition:background .12s}",
    ".dots i.f::after{content:'';width:9px;height:9px;border-radius:50%;background:var(--s-txt)}",
    ".dots.err i{background:color-mix(in srgb,var(--s-red) 22%,transparent)}",
    ".dots.err{animation:sh .4s}",
    "@keyframes sh{20%{transform:translateX(-9px)}40%{transform:translateX(9px)}60%{transform:translateX(-6px)}80%{transform:translateX(6px)}}",
    ".msg{min-height:20px;text-align:center;font-size:14px;color:var(--s-sec);margin-bottom:2px}",
    ".msg.err{color:var(--s-red)}.msg.ok{color:var(--s-green)}",
    ".keys{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:8px auto 12px;max-width:264px}",
    ".key{height:50px;border-radius:12px;background:var(--s-fill);display:flex;align-items:center;justify-content:center;font-family:inherit;font-size:20px;font-weight:500;line-height:1;text-align:center}",
    ".key:active{background:var(--s-fill2);transform:scale(.96)}",
    ".key.ghost{background:none;font-size:14px;font-weight:500;color:var(--s-sec)}",
    ".key.ghost ha-icon{--mdc-icon-size:22px}",
    ".go{display:block;width:100%;max-width:264px;margin:0 auto;padding:12px;border-radius:12px;background:var(--s-blue);color:#fff;font-size:15px;font-weight:600;text-align:center}",
    ".go[disabled]{background:var(--s-fill2);color:var(--s-sec);pointer-events:none}",
    ".linkbtn{color:var(--s-blue);font-size:15px;font-weight:500}",
    ".sheet-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}",
    /* plan */
    ".plan{width:100%;height:auto;display:block;border-radius:16px;background:var(--s-fill)}",
    ".plan text{font-family:inherit;pointer-events:none}",
    ".v3{position:relative;width:100%;aspect-ratio:4/3;border-radius:16px;background:var(--s-fill);overflow:hidden;touch-action:none;cursor:grab}",
    ".v3 canvas{display:block;width:100%;height:100%}",
    ".v3 .hint{position:absolute;left:12px;bottom:10px;font-size:12px;color:var(--s-sec);pointer-events:none}",
    ".floors{display:flex;gap:6px;flex-wrap:wrap;margin:12px 0 0}",
    ".fl{padding:6px 13px;border-radius:15px;background:var(--s-fill);font-size:13.5px;font-weight:500}",
    ".fl.on{background:var(--s-blue);color:#fff}",
    /* journal */
    ".tl{position:relative;padding-left:22px}",
    ".tl::before{content:'';position:absolute;left:5px;top:8px;bottom:8px;width:2px;border-radius:1px;background:var(--s-fill2)}",
    ".ev{position:relative;padding:7px 0 9px}",
    ".ev::before{content:'';position:absolute;left:-22px;top:12px;width:12px;height:12px;border-radius:50%;background:var(--c,var(--s-sec));box-shadow:0 0 0 4px var(--s-card)}",
    ".ev time{display:block;font-size:12px;color:var(--s-sec);font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:.02em}",
    ".ev p{margin:1px 0 0;font-size:15px;line-height:1.3;letter-spacing:-.005em}"
  ].join("");

  class SentinelCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._cfg = {};
      this._hass = null;
      this.shadowRoot.addEventListener("click", this._click.bind(this));
    }
    setConfig(c) { this._cfg = Object.assign({}, c || {}); this._dirty = true; this._render(); }
    set hass(h) { this._hass = h; this._onHass && this._onHass(); if (this._watch) watchResult(this); this._render(); }
    get hass() { return this._hass; }
    getCardSize() { return 3; }
    connectedCallback() {
      var self = this;
      this._dirty = true;
      this._tick = setInterval(function () { if (self._ticks && self._ticks()) self._render(); }, 1000);
      if (this._hass) this._render();
    }
    disconnectedCallback() { clearInterval(this._tick); this._leave && this._leave(); }
    _click(ev) {
      var t = ev.target.closest("[data-a]");
      if (t && this.act) this.act(t.dataset.a, t.dataset, ev);
    }
    _render() {
      if (!this._hass) return;
      this._dirty = false;
      this.shadowRoot.innerHTML = "<style>" + CSS + "</style>" + this.body();
    }
    call(domain, service, data) { return this._hass.callService(domain, service, data); }
  }

  /* ---------- digicode partagé ---------- */
  function keypadHTML(card, opts) {
    var k = card._kp || (card._kp = { code: "", msg: "", cls: "", err: false });
    var dots = "", n = Math.max(4, k.code.length);
    for (var i = 0; i < n; i++) dots += '<i class="' + (i < k.code.length ? "f" : "") + '"></i>';
    var keys = "";
    for (var d = 1; d <= 9; d++) keys += '<button class="key" data-a="digit" data-d="' + d + '">' + d + "</button>";
    keys += '<button class="key ghost" data-a="clear">Effacer</button>' +
      '<button class="key" data-a="digit" data-d="0">0</button>' +
      '<button class="key ghost" data-a="back" aria-label="Supprimer"><ha-icon icon="mdi:backspace-outline"></ha-icon></button>';
    var disabled = k.code.length < 4 && !(opts && opts.free);
    return '<div class="dots' + (k.err ? " err" : "") + '">' + dots + '</div><div class="msg ' + k.cls + '">' + esc(k.msg) + '</div><div class="keys">' + keys + "</div>" +
      '<button class="go" data-a="submit"' + (disabled ? " disabled" : "") + ">" + esc(opts && opts.submit || "Valider") + "</button>";
  }
  function keypadAct(card, action, data, submit) {
    var k = card._kp || (card._kp = { code: "", msg: "", cls: "", err: false });
    if (action === "digit") {
      if (k.code.length < 12) k.code += data.d;
      k.msg = ""; k.cls = ""; k.err = false;
    } else if (action === "back") {
      k.code = k.code.slice(0, -1); k.msg = ""; k.cls = ""; k.err = false;
    } else if (action === "clear") {
      k.code = ""; k.msg = ""; k.cls = ""; k.err = false;
    } else if (action === "cancel") {
      k.code = ""; k.msg = ""; k.cls = ""; k.err = false;
      if (card._cancel) card._cancel();
    } else if (action === "submit") {
      var code = k.code;
      k.busy = true;
      Promise.resolve(submit(code)).then(function () {
        k.code = ""; k.msg = "Code accepté"; k.cls = "ok"; k.err = false; k.busy = false; card._render();
        setTimeout(function () { if (k.cls === "ok") { k.msg = ""; k.cls = ""; card._render(); } }, 2200);
      }).catch(function (e) {
        k.code = ""; k.msg = (e && e.message) || "Code incorrect"; k.cls = "err"; k.err = true; k.busy = false; card._render();
      });
    }
    card._render();
  }

  /* ---------- barre de modes partagée (statut + modes, carte mode) ---------- */
  function visibleModes(card, mode) {
    var modes = mode.attributes.modes || [], want = card._cfg.modes;
    if (want && want.length) modes = modes.filter(function (m) { return want.indexOf(m.name) >= 0 || want.indexOf(m.id) >= 0; });
    return modes;
  }
  function modeBar(card, mode, cls) {
    var cur = mode.attributes.mode_id;
    return '<div class="modes ' + (cls || "") + '">' + visibleModes(card, mode).map(function (m, i) {
      return '<button class="mbtn' + (m.id === cur ? " on" : "") + '" data-a="mode" data-id="' + esc(m.id) + '" style="--c:' + esc(m.color || "var(--s-blue)") + '">' +
        '<ha-icon icon="' + modeIcon(m, i) + '"></ha-icon>' + esc(m.name) +
        (m.need_code ? ' <ha-icon icon="mdi:lock" style="--mdc-icon-size:11px;opacity:.55"></ha-icon>' : "") + "</button>";
    }).join("") + "</div>";
  }
  function pendingLeft(mode) {
    var pc = mode.attributes.pending_change;
    return pc ? { name: pc.mode, left: secondsLeft(pc.until) } : null;
  }
  /* résultat d'une demande de validation : affiché une fois, quand il change */
  function watchResult(card) {
    var m = find(card._hass, "mode"); if (!m) return;
    var lr = m.attributes.last_request;
    if (!lr) return;
    if (card._seenSeq === undefined) { card._seenSeq = lr.seq; return; }
    if (lr.seq === card._seenSeq) return;
    card._seenSeq = lr.seq;
    var txt = lr.result === "refused" ? "Changement refusé sur l’appareil"
      : lr.result === "expired" ? "Validation expirée : le mode n’a pas changé"
        : "Changement validé";
    flash(card, txt + " · « " + lr.mode + " »", lr.result === "confirmed" ? "ok" : "bad");
  }
  function noteLine(card, mode) {
    var p = pendingLeft(mode);
    if (p && p.left > 0) return '<div class="s c" style="margin-top:12px;--c:var(--s-orange)">En attente de validation sur ton appareil · « ' + esc(p.name) + ' » · ' + p.left + " s</div>";
    if (card._flash) return '<div class="s c" style="margin-top:12px;--c:' + (card._flashKind === "bad" ? "var(--s-red)" : "var(--s-green)") + '">' + esc(card._flash) + "</div>";
    return "";
  }
  function flash(card, text, kind) {
    card._flashKind = kind || "ok";
    card._flash = text; card._render();
    clearTimeout(card._flashT);
    card._flashT = setTimeout(function () { card._flash = ""; card._flashKind = "ok"; card._render(); }, 7000);
  }
  function codeSheet(card, mode) {
    var m0 = visibleModes(card, mode).filter(function (m) { return m.id === card._asking; })[0] || { name: "" };
    return '<ha-card><div class="sheet-h"><div class="t">Activer « ' + esc(m0.name) + ' »</div><button class="linkbtn" data-a="cancel">Annuler</button></div>' +
      '<div class="s" style="text-align:center">Saisis le code d’armement</div>' + keypadHTML(card, {}) + "</ha-card>";
  }
  /* retourne true si l'action a été traitée */
  function modeAct(card, a, d) {
    if (a === "mode") {
      var mode = find(card._hass, "mode"), m = (mode.attributes.modes || []).filter(function (x) { return x.id === d.id; })[0];
      if (!m || m.id === mode.attributes.mode_id) return true;
      if (m.need_code) { card._asking = m.id; card._kp = null; card._render(); return true; }
      card.call("sentinel", "set_mode", { mode: m.id }).then(function () {
        if (m.need_confirm) flash(card, "Validation envoyée sur ton appareil");
      }).catch(function (e) { flash(card, (e && e.message) || "Action refusée"); });
      return true;
    }
    if (card._asking) {
      card._cancel = function () { card._asking = null; card._kp = null; };
      keypadAct(card, a, d, function (code) {
        var asked = visibleModes(card, find(card._hass, "mode")).filter(function (x) { return x.id === card._asking; })[0] || {};
        return card.call("sentinel", "set_mode", { mode: card._asking, code: code }).then(function () {
          card._asking = null; card._kp = null;
          if (asked.need_confirm) flash(card, "Code accepté · validation envoyée sur ton appareil");
        });
      });
      return true;
    }
    return false;
  }

  /* ---------- statut (option show_modes : petits boutons de mode) ---------- */
  class StatusCard extends SentinelCard {
    constructor() { super(); this._watch = true; }
    _ticks() {
      var m = find(this._hass, "mode"); if (!m) return false;
      var p = pendingLeft(m);
      return !!(secondsLeft(m.attributes.grace_until) || (p && p.left > 0));
    }
    body() {
      var h = this._hass, mode = find(h, "mode"), st = find(h, "status");
      if (!mode || !st) return '<ha-card><div class="empty">Sentinel introuvable</div></ha-card>';
      if (this._asking) return codeSheet(this, mode);
      var level = st.state, grace = secondsLeft(mode.attributes.grace_until);
      var c = function (key) { var e = find(h, key); return e ? Number(e.state) || 0 : 0; };
      var n = c("critical_count") + c("warning_count") + c("info_count");
      var sub, subCol = "";
      if (grace) { sub = "Armement dans " + grace + " s"; subCol = "warning"; }
      else if (level === "ok") sub = "Aucune détection";
      else { sub = LV_LABEL[level] + " · " + plural(n, "déclenché", "déclenchés"); subCol = level; }
      var modes = mode.attributes.modes || [], idx = modes.findIndex(function (m) { return m.id === mode.attributes.mode_id; });
      var active = grace ? "warning" : level;
      var icon = grace ? "mdi:timer-sand" : (level === "ok" ? modeIcon(modes[idx] || {}, idx) : ICON[level]);
      var stats = "";
      if (this._cfg.show_counts === true) {
        stats = '<div class="stats">' + [["critical", "critical_count"], ["warning", "warning_count"], ["info", "info_count"]].map(function (x) {
          var v = c(x[1]);
          return '<div class="stat' + (v ? " hit" : "") + '" style="--c:' + colorOf(x[0]) + '"><b>' + v + "</b><span>" + LV_LABEL[x[0]] + "</span></div>";
        }).join("") + "</div>";
      }
      var bar = (this._cfg.show_modes ? modeBar(this, mode, "tight") : "") + noteLine(this, mode);
      return '<ha-card class="' + (active === "ok" ? "" : "tint") + '" style="--c:' + colorOf(active) + '"><button class="row" data-a="more" style="width:100%">' +
        '<div class="ring' + (active !== "ok" ? " live" : "") + '" style="--c:' + colorOf(active) + '"><ha-icon icon="' + icon + '"></ha-icon></div>' +
        '<div class="sp"><div class="t big">' + esc(mode.state) + '</div><div class="s' + (subCol ? " c" : "") + '" style="--c:' + colorOf(subCol) + '">' + esc(sub) + "</div></div></button>" + stats + bar + "</ha-card>";
    }
    act(a, d) {
      if (a === "more") { var m = find(this._hass, "status"); if (m) moreInfo(this, m.entity_id); return; }
      modeAct(this, a, d);
    }
  }
  /* statut + boutons de mode : la même carte avec show_modes activé par défaut */
  class ControlCard extends StatusCard {
    setConfig(c) { super.setConfig(Object.assign({ show_modes: true }, c || {})); }
  }

  /* ---------- mode ---------- */
  class ModeCard extends SentinelCard {
    constructor() { super(); this._watch = true; }
    _ticks() {
      var m = find(this._hass, "mode"); if (!m) return false;
      var p = pendingLeft(m);
      return !!(secondsLeft(m.attributes.grace_until) || (p && p.left > 0));
    }
    body() {
      var mode = find(this._hass, "mode");
      if (!mode) return '<ha-card><div class="empty">Sentinel introuvable</div></ha-card>';
      if (this._asking) return codeSheet(this, mode);
      var A = mode.attributes, grace = secondsLeft(A.grace_until), cur = A.mode_id;
      var curm = visibleModes(this, mode).filter(function (m) { return m.id === cur; })[0];
      var desc = "";
      if (this._cfg.show_description !== false && curm && curm.desc) desc = '<div class="s" style="margin-top:12px">' + esc(curm.desc) + "</div>";
      if (grace) desc = '<div class="s c" style="margin-top:12px;--c:var(--s-orange)">Délai de sortie : ' + grace + " s</div>";
      var note = noteLine(this, mode);
      return '<ha-card><div class="cap">' + esc(this._cfg.title || "Mode d’alarme") + "</div>" + modeBar(this, mode) + (note || desc) + "</ha-card>";
    }
    act(a, d) { modeAct(this, a, d); }
  }

  /* ---------- digicode ---------- */
  class KeypadCard extends SentinelCard {
    constructor() { super(); this._watch = true; }
    _ticks() {
      var m = find(this._hass, "mode"); var p = m && pendingLeft(m);
      return !!(p && p.left > 0);
    }
    body() {
      var mode = find(this._hass, "mode");
      if (!mode) return '<ha-card><div class="empty">Sentinel introuvable</div></ha-card>';
      var modes = mode.attributes.modes || [], want = this._cfg.modes;
      if (want && want.length) modes = modes.filter(function (m) { return want.indexOf(m.name) >= 0 || want.indexOf(m.id) >= 0; });
      if (!this._target || !modes.some(function (m) { return m.id === this._target; }, this)) {
        var cfgT = this._cfg.target;
        var pick = modes.filter(function (m) { return cfgT && (m.id === cfgT || m.name === cfgT); })[0] ||
          modes.filter(function (m) { return m.id !== mode.attributes.mode_id; })[0] || modes[0];
        this._target = pick && pick.id;
      }
      var self = this;
      var seg = '<div class="seg">' + modes.map(function (m) {
        return '<button data-a="target" data-id="' + esc(m.id) + '" class="' + (m.id === self._target ? "on" : "") + '">' + esc(m.name) + "</button>";
      }).join("") + "</div>";
      var tm = modes.filter(function (m) { return m.id === self._target; })[0] || {};
      var hint = tm.need_code ? "Saisis le code pour activer « " + tm.name + " »" : "Aucun code requis pour « " + (tm.name || "") + " »";
      if (tm.need_confirm) hint += " · à valider ensuite sur ton appareil";
      var note = noteLine(this, mode);
      return '<ha-card><div class="cap">' + esc(this._cfg.title || "Digicode") + "</div>" + seg +
        '<div class="s" style="text-align:center;margin:16px 0 0">' + esc(hint) + "</div>" + (note ? note.replace("margin-top:12px", "margin-top:8px;text-align:center") : "") +
        keypadHTML(this, { submit: tm.need_code ? "Valider" : (tm.need_confirm ? "Demander la validation" : "Activer"), free: !tm.need_code }) + "</ha-card>";
    }
    act(a, d) {
      var self = this;
      if (a === "target") { this._target = d.id; this._kp = null; this._render(); return; }
      keypadAct(this, a, d, function (code) {
        var data = { mode: self._target };
        if (code) data.code = code;
        var tm = (find(self._hass, "mode").attributes.modes || []).filter(function (m) { return m.id === self._target; })[0] || {};
        return self.call("sentinel", "set_mode", data).then(function () {
          if (tm.need_confirm) flash(self, "Validation envoyée sur ton appareil");
        });
      });
    }
  }

  /* ---------- zones ---------- */
  class ZonesCard extends SentinelCard {
    body() {
      var zs = zoneStates(this._hass), want = this._cfg.entities, cfg = this._cfg;
      if (want && want.length) zs = zs.filter(function (z) { return want.indexOf(z.entity_id) >= 0; });
      if (cfg.floor) zs = zs.filter(function (z) { return z.attributes.floor === cfg.floor; });
      zs.sort(function (a, b) {
        return RANK[b.state] - RANK[a.state] || String(a.attributes.zone_name).localeCompare(String(b.attributes.zone_name));
      });
      var hot = zs.filter(function (z) { return z.state !== "ok"; }).length;
      var limit = cfg.max || 6;
      var shown = this._all ? zs : zs.slice(0, Math.max(limit, hot));
      var tiles = shown.map(function (z) {
        var A = z.attributes, lv = z.state, n = A.triggered || 0;
        var sub = lv === "ok" ? (A.total ? plural(A.total, "capteur", "capteurs") : "Aucun capteur") : LV_LABEL[lv] + " · " + n;
        return '<button class="tile' + (lv !== "ok" ? " hot" : "") + '" data-a="more" data-e="' + esc(z.entity_id) + '" style="--c:' + colorOf(lv) + '">' +
          '<span class="ic"><ha-icon icon="' + (lv === "ok" ? (A.outdoor ? "mdi:tree" : "mdi:floor-plan") : ICON[lv]) + '"></ha-icon></span>' +
          '<span class="tx"><b>' + esc(A.zone_name || z.entity_id) + "</b><span>" + esc(sub) + "</span></span></button>";
      }).join("");
      var more = zs.length > shown.length ? '<button class="more" data-a="all">Voir les ' + zs.length + " zones</button>"
        : (this._all && zs.length > limit ? '<button class="more" data-a="less">Réduire</button>' : "");
      return '<ha-card><div class="cap">' + esc(cfg.title || "Zones") + '<span class="r">' + (hot ? plural(hot, "active", "actives") : "Tout est calme") + "</span></div>" +
        '<div class="tiles">' + (tiles || '<div class="empty" style="grid-column:1/-1">Aucune zone</div>') + "</div>" + more + "</ha-card>";
    }
    act(a, d) {
      if (a === "more") moreInfo(this, d.e);
      else if (a === "all") { this._all = true; this._render(); }
      else if (a === "less") { this._all = false; this._render(); }
    }
  }

  /* ---------- acquittement ---------- */
  var TYPE_ICON = { window: "mdi:window-closed-variant", door: "mdi:door", motion: "mdi:motion-sensor", smoke: "mdi:smoke-detector", leak: "mdi:water-alert", presence: "mdi:account" };
  class AckCard extends SentinelCard {
    _items() { var a = find(this._hass, "active"); return (a && a.attributes.items) || []; }
    _ticks() { return this._items().some(function (i) { return i.until; }); }
    body() {
      var items = this._items(), n = items.filter(function (i) { return i.status !== "acked"; }).length;
      var rows = items.map(function (i) {
        var left = secondsLeft(i.until), col = i.status === "pending" ? "warning" : (i.status === "acked" ? "ok" : i.level);
        var sub = i.status === "pending" ? (i.kind === "exit" ? "Délai de sortie" : "Délai d’entrée") + " · " + left + " s"
          : i.status === "acked" ? "Acquitté · reprise dans " + left + " s"
            : LV_LABEL[i.level] + (i.zone ? " · " + i.zone : "");
        var icon = i.status === "pending" ? "mdi:timer-sand" : (TYPE_ICON[i.type] || ICON[i.level] || "mdi:alert");
        return '<div class="item' + (i.status === "acked" ? " acked" : "") + '" style="--c:' + colorOf(col) + '"><span class="ic"><ha-icon icon="' + icon + '"></ha-icon></span>' +
          '<span class="tx"><b>' + esc(i.name) + "</b><span>" + esc(sub) + "</span></span>" +
          (i.status === "acked" ? "" : '<button class="pillbtn" data-a="ack" data-e="' + esc(i.entity_id || i.id) + '">Acquitter</button>') + "</div>";
      }).join("");
      var right = n > 1 ? '<button class="pillbtn soft" data-a="all">Tout acquitter</button>' : (n ? '<span class="count">' + n + "</span>" : "");
      var head = '<div class="cap">' + esc(this._cfg.title || "À traiter") + right + "</div>";
      if (!items.length) {
        return '<ha-card>' + head + '<div class="empty"><ha-icon icon="mdi:check-circle"></ha-icon>Rien à traiter</div></ha-card>';
      }
      return "<ha-card>" + head + '<div class="stack">' + rows + "</div></ha-card>";
    }
    act(a, d) {
      if (a === "ack") this.call("sentinel", "acknowledge", { entity_id: d.e });
      else if (a === "all") this.call("sentinel", "acknowledge", {});
    }
  }

  /* ---------- chips ---------- */
  class ChipsCard extends SentinelCard {
    _ticks() { var m = find(this._hass, "mode"); return !!(m && secondsLeft(m.attributes.grace_until)); }
    body() {
      var h = this._hass, mode = find(h, "mode"), st = find(h, "status"), act = find(h, "active");
      if (!mode || !st) return '<ha-card><div class="empty">Sentinel introuvable</div></ha-card>';
      var which = this._cfg.chips || ["mode", "status", "zones", "ack"], out = [];
      var hideEmpty = this._cfg.hide_empty !== false;
      var grace = secondsLeft(mode.attributes.grace_until);
      var modes = mode.attributes.modes || [], idx = modes.findIndex(function (m) { return m.id === mode.attributes.mode_id; });
      var items = (act && act.attributes.items) || [], todo = items.filter(function (i) { return i.status !== "acked"; });
      var hot = zoneStates(h).filter(function (z) { return z.state !== "ok"; });
      which.forEach(function (w) {
        if (w === "mode") {
          out.push('<button class="chip" data-a="more" data-e="' + esc(mode.entity_id) + '" style="--c:' + esc((modes[idx] || {}).color || "var(--s-blue)") + '"><span class="ic"><ha-icon icon="' + modeIcon(modes[idx] || {}, idx) + '"></ha-icon></span>' +
            esc(mode.state) + (grace ? " · " + grace + " s" : "") + "</button>");
        }
        if (w === "status") {
          var lv = st.state, n = todo.length;
          if (lv === "ok" && !n) {
            out.push('<button class="chip" data-a="more" data-e="' + esc(st.entity_id) + '" style="--c:var(--s-green)"><span class="ic"><ha-icon icon="mdi:shield-check"></ha-icon></span>Calme</button>');
          } else {
            var lvl = lv === "ok" ? "warning" : lv;
            out.push('<button class="chip solid" data-a="more" data-e="' + esc(st.entity_id) + '" style="--c:' + colorOf(lvl) + '"><span class="ic"><ha-icon icon="' + ICON[lvl] + '"></ha-icon></span>' +
              (lv === "ok" ? "En détection" : LV_LABEL[lv]) + "</button>");
          }
        }
        if (w === "zones") {
          if (hot.length || !hideEmpty) {
            var worst = hot.some(function (z) { return z.state === "critical"; }) ? "critical" : hot.some(function (z) { return z.state === "warning"; }) ? "warning" : hot.length ? "info" : "ok";
            out.push('<button class="chip" data-a="zone" style="--c:' + colorOf(worst) + '"><span class="ic"><ha-icon icon="mdi:floor-plan"></ha-icon></span>' +
              (hot.length ? plural(hot.length, "zone", "zones") : "Zones calmes") + "</button>");
          }
        }
        if (w === "ack") {
          if (todo.length || !hideEmpty) {
            out.push('<button class="chip" data-a="ack" style="--c:var(--s-blue)"><span class="ic"><ha-icon icon="mdi:check"></ha-icon></span>' +
              (todo.length ? "Acquitter · " + todo.length : "Rien à acquitter") + "</button>");
          }
        }
      });
      return '<ha-card class="bare"><div class="chips">' + out.join("") + "</div></ha-card>";
    }
    act(a, d) {
      if (a === "more") moreInfo(this, d.e);
      else if (a === "ack") this.call("sentinel", "acknowledge", {});
      else if (a === "zone") {
        var z = zoneStates(this._hass).filter(function (x) { return x.state !== "ok"; })[0];
        if (z) moreInfo(this, z.entity_id);
      }
    }
  }

  /* ---------- plan 2D / maquette 3D déplaçable ---------- */
  function centroid(pts) {
    var a = 0, cx = 0, cz = 0;
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i], q = pts[(i + 1) % pts.length], c = p[0] * q[1] - q[0] * p[1];
      a += c; cx += (p[0] + q[0]) * c; cz += (p[1] + q[1]) * c;
    }
    if (Math.abs(a) < 1e-9) return { x: pts[0][0], z: pts[0][1] };
    return { x: cx / (3 * a), z: cz / (3 * a) };
  }
  function loadThree() {
    if (window.THREE) return Promise.resolve(window.THREE);
    if (window.__sentinelThree) return window.__sentinelThree;
    var url = window.SENTINEL_THREE_URL || "/sentinel_static/three.min.js";
    window.__sentinelThree = new Promise(function (res) {
      var s = document.createElement("script");
      s.src = url; s.onload = function () { res(window.THREE || null); }; s.onerror = function () { res(null); };
      document.head.appendChild(s);
    });
    return window.__sentinelThree;
  }

  /* scène 3D : même logique que le panneau (dalles, murs translucides, éléments, ondes), sans iframe */
  function Scene3D(host, THREE) {
    this.T = THREE; this.host = host; this.theta = 0.78; this.phi = 1.0; this.r = 24; this.rmin = 8; this.rmax = 70;
    this.waves = []; this.sig = "";
    var T = THREE;
    this.renderer = new T.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    host.insertBefore(this.renderer.domElement, host.firstChild);
    this.scene = new T.Scene();
    this.cam = new T.PerspectiveCamera(32, 4 / 3, 0.1, 300);
    this.scene.add(new T.AmbientLight(0xffffff, 0.85));
    var dl = new T.DirectionalLight(0xffffff, 0.6); dl.position.set(-8, 20, 12); this.scene.add(dl);
    this.root = new T.Group(); this.scene.add(this.root);
    this.target = { x: 5, y: 2, z: 4 };
    this._bind();
    this.resize();
    var self = this;
    if (window.ResizeObserver) { this.ro = new ResizeObserver(function () { self.resize(); }); this.ro.observe(host); }
    this._loop = function (ms) {
      if (self.dead) return;
      self.raf = requestAnimationFrame(self._loop);
      if (self.visible === false) return;
      self.tick(ms / 1000);
      self.draw();
    };
    if (window.IntersectionObserver) { this.io = new IntersectionObserver(function (es) { self.visible = es[0].isIntersecting; }); this.io.observe(host); }
    this.raf = requestAnimationFrame(this._loop);
  }
  Scene3D.prototype._bind = function () {
    var self = this, el = this.host, pts = new Map(), pinch = 0;
    el.addEventListener("pointerdown", function (ev) {
      try { el.setPointerCapture(ev.pointerId); } catch (e) { /* ok */ }
      pts.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (pts.size === 2) { var a = Array.from(pts.values()); pinch = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y); }
      el.style.cursor = "grabbing";
      var h = el.querySelector(".hint"); if (h) h.style.display = "none";
    });
    el.addEventListener("pointermove", function (ev) {
      var p = pts.get(ev.pointerId); if (!p) return;
      if (pts.size === 1) {
        self.theta -= (ev.clientX - p.x) * 0.0065;
        self.phi = Math.min(1.5, Math.max(0.25, self.phi - (ev.clientY - p.y) * 0.0055));
      }
      p.x = ev.clientX; p.y = ev.clientY;
      if (pts.size === 2) {
        var a = Array.from(pts.values()), d = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
        if (pinch) self.r = Math.min(self.rmax, Math.max(self.rmin, self.r * pinch / d));
        pinch = d;
      }
    });
    function up(ev) { pts.delete(ev.pointerId); pinch = 0; el.style.cursor = "grab"; }
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    el.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      self.r = Math.min(self.rmax, Math.max(self.rmin, self.r * (1 + ev.deltaY * 0.0012)));
    }, { passive: false });
  };
  Scene3D.prototype.resize = function () {
    var w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.cam.aspect = w / h; this.cam.updateProjectionMatrix();
  };
  Scene3D.prototype.cssColor = function (name, fallback) {
    var v = getComputedStyle(this.host).getPropertyValue(name).trim();
    return v || fallback;
  };
  Scene3D.prototype.label = function (text, color) {
    var T = this.T, cv = document.createElement("canvas"); cv.width = 512; cv.height = 96;
    var x = cv.getContext("2d");
    x.font = "600 44px -apple-system,'SF Pro Text',system-ui,sans-serif"; x.textAlign = "center"; x.textBaseline = "middle";
    x.fillStyle = color; x.fillText(text, 256, 48);
    var s = new T.Sprite(new T.SpriteMaterial({ map: new T.CanvasTexture(cv), transparent: true, depthTest: false, opacity: 0.85 }));
    s.scale.set(3.2, 0.6, 1); s.renderOrder = 6; return s;
  };
  /* data : configuration (floors, rooms, ents) ; zoneLevel(roomId) ; items : éléments actifs */
  Scene3D.prototype.build = function (data, zoneLevel, items) {
    var T = this.T, self = this;
    var sig = JSON.stringify([data.rooms.length, items.map(function (i) { return [i.id, i.status, i.level]; }),
      data.rooms.map(function (r) { return zoneLevel(r.id); })]);
    if (sig === this.sig) return;
    this.sig = sig;
    while (this.root.children.length) {
      var o = this.root.children[0];
      o.traverse(function (n) { if (n.geometry) n.geometry.dispose(); if (n.material) { if (n.material.map) n.material.map.dispose(); n.material.dispose(); } });
      this.root.remove(o);
    }
    this.waves = [];
    var colors = {
      critical: new T.Color(this.cssColor("--s-red", "#ff3b30")), warning: new T.Color(this.cssColor("--s-orange", "#ff9500")),
      info: new T.Color(this.cssColor("--s-blue", "#007aff")), ok: new T.Color(this.cssColor("--s-green", "#34c759"))
    };
    var txt = this.cssColor("--s-txt", "#000"), sec = new T.Color(this.cssColor("--s-sec", "#8e8e93"));
    var slabBase = sec.clone().lerp(new T.Color(0xffffff), 0.55);
    var floors = data.floors.slice().sort(function (a, b) { return a.level - b.level; });
    var level = {}; floors.forEach(function (f) { level[f.id] = f.level; });
    var PITCH = 4.2, x0 = 1e9, x1 = -1e9, z0 = 1e9, z1 = -1e9, ymax = 0;
    var byId = {}; data.rooms.forEach(function (r) { byId[r.id] = r; });
    data.rooms.forEach(function (r) {
      if (!r.pts) return;
      var y0 = (level[r.floor] || 0) * PITCH, lv = zoneLevel(r.id);
      r.pts.forEach(function (p) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); z0 = Math.min(z0, p[1]); z1 = Math.max(z1, p[1]); });
      ymax = Math.max(ymax, y0 + 2.6);
      var shp = new T.Shape();
      r.pts.forEach(function (p, i) { if (i) shp.lineTo(p[0], p[1]); else shp.moveTo(p[0], p[1]); });
      var col = slabBase.clone(); if (lv !== "ok") col.lerp(colors[lv], 0.55);
      var slab = new T.Mesh(new T.ExtrudeGeometry(shp, { depth: 0.14, bevelEnabled: false }), new T.MeshLambertMaterial({ color: col }));
      slab.rotation.x = Math.PI / 2; slab.position.y = y0 + 0.07;
      self.root.add(slab);
      var wm = new T.MeshLambertMaterial({ color: sec, transparent: true, opacity: 0.3, depthWrite: false });
      if (!r.outdoor) r.pts.forEach(function (p, i) {
        if (!(r.wl || [])[i]) return;
        var q = r.pts[(i + 1) % r.pts.length], dx = q[0] - p[0], dz = q[1] - p[1], len = Math.hypot(dx, dz);
        if (len < 0.01) return;
        var m = new T.Mesh(new T.BoxGeometry(len, 2.5, 0.08), wm);
        m.position.set((p[0] + q[0]) / 2, y0 + 1.25, (p[1] + q[1]) / 2);
        m.rotation.y = -Math.atan2(dz, dx);
        self.root.add(m);
      });
      var c = centroid(r.pts), lb = self.label(r.name, txt);
      lb.position.set(c.x, y0 + 0.4, c.z); self.root.add(lb);
    });
    var act = {}; items.forEach(function (i) { act[i.id] = i; });
    var neutral = sec.clone().lerp(new T.Color(0x000000), 0.25);
    (data.ents || []).forEach(function (e) {
      var y0 = (level[e.floor] || 0) * PITCH, it = act[e.id], r = byId[e.room];
      var st = it ? (it.status === "pending" ? "warning" : it.status === "acked" ? "ok" : it.level) : null;
      var col = st ? colors[st] : neutral, open = e.room != null && e.edge != null;
      var geo, y, W = e.w || 0.9;
      if (open) {
        var h = (e.type === "window" || e.type === "gate") ? 1.1 : 2.1; y = e.type === "window" ? 1.45 : h / 2;
        geo = new T.BoxGeometry(W, h, 0.14);
      } else if (e.d && e.h) { geo = new T.BoxGeometry(e.w || 0.6, e.h, e.d); y = e.h / 2; }
      else { geo = new T.SphereGeometry(0.2, 14, 10); y = 1.9; }
      var lit = st && st !== "ok";
      var m = new T.Mesh(geo, new T.MeshLambertMaterial({ color: col, emissive: lit ? col : 0x000000, emissiveIntensity: lit ? 0.5 : 0 }));
      m.position.set(e.x, y0 + y, e.z);
      if (open && r && r.pts && r.pts[e.edge]) {
        var a = r.pts[e.edge], b = r.pts[(e.edge + 1) % r.pts.length];
        m.rotation.y = -Math.atan2(b[1] - a[1], b[0] - a[0]);
      }
      self.root.add(m);
      var big = open || !!(e.d && e.h);
      if (!big) {
        var stem = new T.Mesh(new T.CylinderGeometry(0.015, 0.015, y, 6), new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.5 }));
        stem.position.set(e.x, y0 + y / 2, e.z); self.root.add(stem);
      }
      if (lit) {
        var n = big ? 2 : 3, w = { type: big ? "sonar" : "ripples", level: st, pending: it.status === "pending", items: [] };
        for (var k = 0; k < n; k++) {
          var wm2 = big
            ? new T.Mesh(new T.SphereGeometry(1, 18, 12), new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0, depthWrite: false }))
            : new T.Mesh(new T.RingGeometry(0.9, 1, 48), new T.MeshBasicMaterial({ color: col, transparent: true, opacity: 0, side: T.DoubleSide, depthWrite: false }));
          if (big) wm2.position.set(e.x, y0 + y, e.z); else { wm2.rotation.x = -Math.PI / 2; wm2.position.set(e.x, y0 + 0.16, e.z); }
          self.root.add(wm2); w.items.push(wm2);
        }
        self.waves.push(w);
      }
    });
    if (x0 < x1) {
      this.target = { x: (x0 + x1) / 2, y: ymax / 2, z: (z0 + z1) / 2 };
      var span = Math.max(x1 - x0, z1 - z0, ymax);
      this.rmin = span * 0.7; this.rmax = span * 3.2;
      if (!this._fit) { this.r = span * 1.7; this._fit = true; }
    }
  };
  var WCFG = { info: { P: 2.8, n: 1 }, warning: { P: 1.8, n: 2 }, critical: { P: 1.15, n: 3 } };
  Scene3D.prototype.tick = function (s) {
    this.waves.forEach(function (w) {
      var c = WCFG[w.level] || WCFG.info, P = c.P * (w.pending ? 1.7 : 1), n = w.pending ? 1 : c.n;
      w.items.forEach(function (m, i) {
        var on = w.type === "sonar" ? i < (n > 1 ? 2 : 1) : i < n;
        m.visible = on; if (!on) return;
        var ph = ((s / P) + (w.type === "sonar" ? i * 0.5 : i / n)) % 1;
        if (w.type === "sonar") { m.scale.setScalar(0.25 + ph * (w.pending ? 1.7 : 2.4)); m.material.opacity = Math.pow(1 - ph, 1.6) * (w.pending ? 0.18 : 0.34); }
        else { var r = 0.3 + ph * (w.pending ? 2.4 : 3.4); m.scale.set(r, r, 1); m.material.opacity = (1 - ph) * (w.pending ? 0.45 : 0.85); }
      });
    });
  };
  Scene3D.prototype.draw = function () {
    var s = Math.sin(this.phi), t = this.target;
    this.cam.position.set(t.x + this.r * s * Math.sin(this.theta), t.y + this.r * Math.cos(this.phi), t.z + this.r * s * Math.cos(this.theta));
    this.cam.lookAt(t.x, t.y, t.z);
    this.renderer.render(this.scene, this.cam);
  };
  Scene3D.prototype.destroy = function () {
    this.dead = true; cancelAnimationFrame(this.raf);
    if (this.ro) this.ro.disconnect();
    if (this.io) this.io.disconnect();
    try { this.renderer.dispose(); } catch (e) { /* ok */ }
  };

  class PlanCard extends SentinelCard {
    constructor() { super(); this._view = null; this._floor = null; this._data = null; this._loading = false; this._scene = null; }
    _leave() { if (this._scene) { this._scene.destroy(); this._scene = null; } }
    _onHass() {
      var self = this;
      if (!this._data && !this._loading && this._hass.callWS) {
        this._loading = true;
        this._hass.callWS({ type: "sentinel/get_config" }).then(function (r) { self._data = r.config; self._loading = false; self._dirty = true; self._render(); })
          .catch(function () { self._loading = false; self._data = { floors: [], rooms: [], ents: [] }; self._dirty = true; self._render(); });
      }
    }
    _zoneLevel(roomId) {
      var z = zoneStates(this._hass).filter(function (s) { return s.attributes.room_id === roomId; })[0];
      return z ? z.state : "ok";
    }
    _items() { var a = find(this._hass, "active"); return (a && a.attributes.items) || []; }
    _render() {
      if (!this._hass) return;
      var v = (this._cfg.view === "3d" || this._cfg.view === "2d") ? this._cfg.view : (this._view || (this._cfg.default_view === "3d" ? "3d" : "2d"));
      // la maquette 3D n'est pas recréée à chaque changement d'état : on met seulement la scène à jour
      if (v === "3d" && this._scene && !this._dirty && this._data) {
        this._scene.build(this._data, this._zoneLevel.bind(this), this._items());
        return;
      }
      this._leave();
      this._dirty = false;
      this.shadowRoot.innerHTML = "<style>" + CSS + "</style>" + this.body();
      if (v === "3d" && this._data) this._mount3d();
    }
    _mount3d() {
      var host = this.shadowRoot.querySelector(".v3"), self = this;
      if (!host) return;
      loadThree().then(function (THREE) {
        if (!THREE) { host.innerHTML = '<div class="empty" style="padding-top:40%">Maquette 3D indisponible</div>'; return; }
        if (!host.isConnected || self._scene) return;
        self._scene = new Scene3D(host, THREE);
        self._scene.build(self._data, self._zoneLevel.bind(self), self._items());
      });
    }
    body() {
      var cfg = this._cfg, data = this._data, h = this._hass;
      var fixed = cfg.view === "3d" || cfg.view === "2d";
      var view = fixed ? cfg.view : (this._view || (cfg.default_view === "3d" ? "3d" : "2d"));
      if (!data) return '<ha-card><div class="empty">Chargement du plan…</div></ha-card>';
      var floors = (data.floors || []).slice().sort(function (a, b) { return b.level - a.level; });
      var fl = this._floor || cfg.floor || (floors.length ? floors[floors.length - 1].id : "");
      var seg = '<div class="seg" style="width:128px;margin:-4px 0"><button data-a="view" data-v="2d" class="' + (view === "2d" ? "on" : "") + '">2D</button><button data-a="view" data-v="3d" class="' + (view === "3d" ? "on" : "") + '">3D</button></div>';
      var head = '<div class="cap">' + esc(cfg.title || "Plan") + (fixed ? "" : seg) + "</div>";
      var floorsHTML = floors.length > 1 && view === "2d"
        ? '<div class="floors">' + floors.map(function (f) { return '<button class="fl' + (f.id === fl ? " on" : "") + '" data-a="floor" data-f="' + esc(f.id) + '">' + esc(f.name) + "</button>"; }).join("") + "</div>" : "";
      var body = view === "3d" ? '<div class="v3"><span class="hint">Glisser pour tourner · molette ou pincer pour zoomer</span></div>' : this._plan2d(data, fl, h);
      return "<ha-card>" + head + body + floorsHTML + "</ha-card>";
    }
    _plan2d(data, fl, h) {
      var rooms = (data.rooms || []).filter(function (r) { return r.floor === fl && r.pts; });
      if (!rooms.length) return '<div class="empty">Aucune pièce sur cet étage</div>';
      var x0 = 1e9, z0 = 1e9, x1 = -1e9, z1 = -1e9;
      rooms.forEach(function (r) { r.pts.forEach(function (p) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); z0 = Math.min(z0, p[1]); z1 = Math.max(z1, p[1]); }); });
      var pad = 0.6, S = 40, W = (x1 - x0 + 2 * pad) * S, H = (z1 - z0 + 2 * pad) * S;
      var X = function (v) { return ((v - x0 + pad) * S).toFixed(1); }, Z = function (v) { return ((v - z0 + pad) * S).toFixed(1); };
      var svg = '<svg class="plan" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Plan">';
      var self = this;
      rooms.forEach(function (r) {
        var lv = self._zoneLevel(r.id), fill = lv === "ok" ? "var(--s-card)" : "color-mix(in srgb," + colorOf(lv) + " 26%,var(--s-card))";
        var pts = r.pts.map(function (p) { return X(p[0]) + "," + Z(p[1]); }).join(" ");
        svg += '<polygon points="' + pts + '" fill="' + fill + '" stroke="var(--s-sec)" stroke-width="1" stroke-opacity=".45" stroke-linejoin="round"/>';
        (r.wl || []).forEach(function (w, i) {
          if (!w) return;
          var a = r.pts[i], b = r.pts[(i + 1) % r.pts.length];
          svg += '<line x1="' + X(a[0]) + '" y1="' + Z(a[1]) + '" x2="' + X(b[0]) + '" y2="' + Z(b[1]) + '" stroke="var(--s-txt)" stroke-width="2.2" stroke-linecap="round" opacity=".6"/>';
        });
        var c = centroid(r.pts);
        svg += '<text x="' + X(c.x) + '" y="' + Z(c.z) + '" text-anchor="middle" dominant-baseline="middle" font-size="12.5" font-weight="600" fill="var(--s-txt)">' + esc(r.name) + "</text>";
      });
      self._items().forEach(function (i) {
        var e = (data.ents || []).filter(function (x) { return x.id === i.id; })[0];
        if (!e || e.floor !== fl) return;
        var col = i.status === "pending" ? "warning" : i.status === "acked" ? "ok" : i.level;
        svg += '<circle cx="' + X(e.x) + '" cy="' + Z(e.z) + '" r="14" fill="' + colorOf(col) + '" opacity=".22"/><circle cx="' + X(e.x) + '" cy="' + Z(e.z) + '" r="6.5" fill="' + colorOf(col) + '" stroke="var(--s-card)" stroke-width="2"/>';
      });
      return svg + "</svg>";
    }
    act(a, d) {
      if (a === "view") { this._view = d.v; this._dirty = true; this._render(); }
      else if (a === "floor") { this._floor = d.f; this._dirty = true; this._render(); }
    }
  }

  /* ---------- journal ---------- */
  class JournalCard extends SentinelCard {
    body() {
      var j = find(this._hass, "journal"), ev = (j && j.attributes.events) || [], max = this._cfg.max || 5;
      var rows = ev.slice(0, max).map(function (e) {
        var c = e.level ? colorOf(e.level) : "var(--s-sec)";
        return '<div class="ev" style="--c:' + c + '"><time>' + esc(String(e.time || "").slice(0, 5)) + "</time><p>" + esc(e.text) + "</p></div>";
      }).join("");
      return '<ha-card><div class="cap">' + esc(this._cfg.title || "Journal") + '<span class="r">Aujourd’hui</span></div>' +
        (rows ? '<div class="tl">' + rows + "</div>" : '<div class="empty">Aucun évènement</div>') + "</ha-card>";
    }
  }

  var CARDS = [
    ["sentinel-status-card", StatusCard, "Sentinel · Statut", "Mode et état global (compteurs et boutons de mode en option)"],
    ["sentinel-control-card", ControlCard, "Sentinel · Statut et modes", "Statut avec petits boutons pour activer les modes"],
    ["sentinel-mode-card", ModeCard, "Sentinel · Mode", "Changer de mode (avec code si requis)"],
    ["sentinel-keypad-card", KeypadCard, "Sentinel · Digicode", "Armer ou désarmer avec un code"],
    ["sentinel-zones-card", ZonesCard, "Sentinel · Zones", "Zones et niveau de chacune"],
    ["sentinel-ack-card", AckCard, "Sentinel · À traiter", "Éléments déclenchés ou en détection, acquittement"],
    ["sentinel-chips-card", ChipsCard, "Sentinel · Chips", "Mode, statut, zones, acquitter"],
    ["sentinel-plan-card", PlanCard, "Sentinel · Plan", "Plan 2D et maquette 3D déplaçable"],
    ["sentinel-journal-card", JournalCard, "Sentinel · Journal", "Derniers évènements"]
  ];
  window.customCards = window.customCards || [];
  CARDS.forEach(function (c) {
    if (!customElements.get(c[0])) customElements.define(c[0], c[1]);
    window.customCards.push({ type: c[0], name: c[2], description: c[3], preview: false });
  });
})();
