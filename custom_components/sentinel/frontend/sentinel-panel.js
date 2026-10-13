/*
 * Panneau Sentinel : enveloppe fine autour de l'application (index.html).
 * L'application tourne dans une iframe de même origine et reçoit l'objet
 * `hass` de Home Assistant via window.sentinelHass.
 */
class SentinelPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._narrow = false;
    this._frame = null;
  }

  connectedCallback() {
    if (this._frame) return;
    this.style.cssText = "display:flex;flex-direction:column;height:100%;min-height:100vh;";

    this._bar = document.createElement("div");
    this._bar.style.cssText =
      "display:none;align-items:center;gap:8px;height:48px;padding:0 8px;" +
      "background:var(--app-header-background-color,#03a9f4);color:var(--app-header-text-color,#fff);" +
      "font:500 18px var(--paper-font-body1_-_font-family,sans-serif);flex:none;";
    const menu = document.createElement("button");
    menu.textContent = "☰";
    menu.setAttribute("aria-label", "Menu");
    menu.style.cssText =
      "background:none;border:0;color:inherit;font-size:22px;cursor:pointer;padding:6px 10px;";
    menu.addEventListener("click", () =>
      this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true }))
    );
    const title = document.createElement("span");
    title.textContent = "Sentinel";
    this._bar.append(menu, title);

    this._frame = document.createElement("iframe");
    this._frame.src = "/sentinel_static/index.html";
    this._frame.title = "Sentinel";
    this._frame.style.cssText = "border:0;width:100%;flex:1;min-height:0;background:transparent;";
    this._frame.addEventListener("load", () => this._push());

    this.append(this._bar, this._frame);
    this._applyNarrow();
  }

  set hass(hass) {
    this._hass = hass;
    this._push();
  }

  set narrow(value) {
    this._narrow = !!value;
    this._applyNarrow();
  }

  // Propriétés fournies par Home Assistant, non utilisées ici.
  set route(_v) {}
  set panel(_v) {}

  _applyNarrow() {
    if (this._bar) this._bar.style.display = this._narrow ? "flex" : "none";
  }

  _push() {
    const win = this._frame && this._frame.contentWindow;
    if (!win || !this._hass) return;
    try {
      win.sentinelHass = this._hass;
      if (typeof win.sentinelOnHass === "function") win.sentinelOnHass(this._hass);
      const dark = this._hass.themes && this._hass.themes.darkMode;
      win.document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    } catch (_err) {
      /* iframe pas encore prête : le prochain set hass réessaiera */
    }
  }
}

customElements.define("sentinel-panel", SentinelPanel);
