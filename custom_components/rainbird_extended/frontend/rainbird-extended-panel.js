const DOMAIN = "rainbird_extended";
const STOP_ACTION_STOP_IRRIGATION = "stop_irrigation";
const STOP_ACTION_TURN_OFF = "stop_and_turn_off";

// --- Yard map ---------------------------------------------------------
// Zone outlines drawn over an aerial photo (or a plain background), with each
// zone lit up while it waters. Leaflet, the map library Home Assistant's own
// map uses, is served from this integration (vendor/leaflet) and loaded the
// first time a yard map is shown.

const YARD_STATIC = `/${DOMAIN}_static/vendor/leaflet`;
const YARD_COLORS = ["#43a047", "#fb8c00", "#8e24aa", "#00acc1", "#d81b60", "#c0ca33", "#6d4c41", "#3949ab"];
const YARD_WATERING = "#1e88e5";
const YARD_BACKGROUNDS = {
  aerial: "Aerial photo (USGS, United States)",
  esri: "Satellite (Esri, needs a free ArcGIS key)",
  apple: "Apple Maps (needs an Apple Developer MapKit key)",
  plain: "No map",
};

let yardLeaflet;
// Leaflet sets window.L when it loads; noConflict() puts back whatever was
// there before (Home Assistant's own copy, if its map has loaded).
const yardLoadLeaflet = () => {
  if (!yardLeaflet) {
    yardLeaflet = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `${YARD_STATIC}/leaflet.js?v=1.9.4`;
      script.onload = () => resolve(window.L.noConflict());
      script.onerror = () => {
        yardLeaflet = undefined;
        reject(new Error("The map couldn't load."));
      };
      document.head.appendChild(script);
    });
  }
  return yardLeaflet;
};

// Apple's MapKit JS, loaded from Apple the first time the Apple Maps
// background is shown. Its tokens are signed in Home Assistant with the
// user's own MapKit key and asked for over the websocket, for this page's
// origin only; the private key never reaches the browser.
let yardMapKit;
let yardMapKitHass;
let yardMapKitPanel;
const yardLoadMapKit = (hass, panel) => {
  yardMapKitHass = hass;
  yardMapKitPanel = panel;
  if (!yardMapKit) {
    yardMapKit = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.apple-mapkit.com/mk/5.x.x/mapkit.js";
      script.crossOrigin = "anonymous";
      script.onload = () => {
        const mapkit = window.mapkit;
        mapkit.init({
          authorizationCallback: (done) => {
            yardMapKitHass
              .callWS({ type: `${DOMAIN}/panel/yard/apple_token`, origin: window.location.origin })
              .then((result) => done(result.token))
              .catch(() => done(""));
          },
        });
        // A wrong Team ID or Key ID shows up here, not when the key is saved.
        mapkit.addEventListener?.("error", (event) => {
          const status = String(event?.status || "");
          yardMapKitPanel?._showNotice(
            status === "Unauthorized"
              ? "Apple Maps didn't accept the MapKit key. Check the Team ID, and that the key has MapKit JS turned on."
              : "Apple Maps couldn't load the map.",
            true,
          );
        });
        resolve(mapkit);
      };
      script.onerror = () => {
        yardMapKit = undefined;
        reject(new Error("Apple Maps couldn't load."));
      };
      document.head.appendChild(script);
    });
  }
  return yardMapKit;
};

// The part of the world a Leaflet view shows, as MapKit's MapRect numbers
// (0–1 across the Web Mercator world). Both maps use Web Mercator, so the
// Apple map can sit under Leaflet showing exactly the same area.
const yardMapRect = (minX, minY, maxX, maxY, zoom) => {
  const world = 256 * 2 ** zoom;
  return [minX / world, minY / world, (maxX - minX) / world, (maxY - minY) / world];
};

// A dropped .p8 file: its private key, and the Key ID from Apple's file
// name (AuthKey_<Key ID>.p8). Null when it isn't a private key.
const yardP8Key = (fileName, text) => {
  if (!/-----BEGIN PRIVATE KEY-----[\s\S]+-----END PRIVATE KEY-----/.test(text || "")) return null;
  const match = /AuthKey_([A-Z0-9]{10})(?![A-Z0-9])/i.exec(fileName || "");
  return { pem: text.trim(), keyId: match ? match[1].toUpperCase() : "" };
};

// Square meters inside an outline of [lat, lng] points (flat earth: fine for a yard).
const yardAreaM2 = (points) => {
  if (!points || points.length < 3) return 0;
  const lat0 = (points.reduce((sum, p) => sum + p[0], 0) / points.length) * Math.PI / 180;
  const radius = 6371008.8;
  const xy = points.map(([lat, lng]) => [
    (lng * Math.PI / 180) * radius * Math.cos(lat0),
    (lat * Math.PI / 180) * radius,
  ]);
  let total = 0;
  xy.forEach(([x1, y1], i) => {
    const [x2, y2] = xy[(i + 1) % xy.length];
    total += x1 * y2 - x2 * y1;
  });
  return Math.abs(total) / 2;
};

const yardAreaText = (m2, imperial) => {
  if (!m2) return "";
  if (imperial) return `${Math.round(m2 * 10.7639).toLocaleString()} sq ft`;
  return `${Math.round(m2).toLocaleString()} m²`;
};

// Whole minutes until an end time, or null when it's past or unknown.
const yardMinutesLeft = (endIso, now = Date.now()) => {
  const end = Date.parse(endIso || "");
  if (Number.isNaN(end) || end <= now) return null;
  return Math.max(1, Math.ceil((end - now) / 60000));
};

// The tile layer for a background, or null for none.
const yardTiles = (background, key) => {
  if (background === "aerial") {
    return {
      url: "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
      attribution: "USDA, USGS The National Map: Orthoimagery",
      maxNativeZoom: 16,
    };
  }
  if (background === "esri" && key) {
    return {
      url: `https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token=${encodeURIComponent(key)}`,
      attribution: "Powered by Esri · Esri, Maxar, Earthstar Geographics, and the GIS User Community",
      maxNativeZoom: 19,
    };
  }
  return null;
};

const YARD_STYLE = `
  .yard{margin-top:22px}
  .yard-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:10px}
  .yard-head h3{margin:0;flex:1 1 auto}
  .yard-head select{width:auto;min-width:0;max-width:100%;flex:0 1 auto;text-overflow:ellipsis}
  .yard select,.yard input[type=text]{font:inherit;font-size:14px;padding:7px 10px;border-radius:8px;border:1px solid var(--border);background:var(--card-background-color,#fff);color:var(--primary-text-color)}
  .yard-map{height:460px;border-radius:12px;overflow:hidden;border:1px solid var(--border);background:var(--secondary-background-color,#e5e5e5);position:relative;z-index:0}
  .yard-apple,.yard-leaflet{position:absolute;inset:0}
  .yard-apple{z-index:0;pointer-events:none}.yard-apple[hidden]{display:none}
  .yard-leaflet{z-index:1}
  .yard-leaflet.over-apple.leaflet-container{background:transparent}
  .yard-apple-form{display:grid;gap:8px;margin-bottom:10px}
  .yard-apple-form .ids{display:flex;flex-wrap:wrap;gap:8px}
  .yard-apple-form input{flex:1 1 140px}
  .yard-drop{display:flex;flex-direction:column;align-items:center;gap:4px;padding:18px 12px;border:2px dashed var(--border);border-radius:12px;text-align:center;cursor:pointer;font-size:14px}
  .yard-drop span{font-size:13px;color:var(--secondary-text-color)}
  .yard-drop.over{border-color:var(--blue);background:color-mix(in srgb,var(--blue) 8%,transparent)}
  .yard-drop.loaded{border-style:solid;border-color:var(--green,#43a047)}
  .yard-apple-form details textarea{width:100%;box-sizing:border-box;margin-top:6px}
  .yard-apple-form summary{cursor:pointer}
  .yard-apple-form textarea{font:12px/1.4 monospace;min-height:90px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--card-background-color,#fff);color:var(--primary-text-color)}
  .yard-map.drawing{cursor:crosshair}
  .yard-tools{display:grid;gap:8px;margin:0 0 10px}
  .yard-zone{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:10px}
  .yard-zone .dot{width:14px;height:14px;border-radius:50%;flex:none}
  .yard-zone .name{flex:1 1 140px;font-weight:500}
  .yard-zone.active{border-color:var(--blue);box-shadow:inset 0 0 0 1px var(--blue)}
  .yard-note{font-size:13px;color:var(--secondary-text-color);margin-top:8px}
  .yard-key{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
  .yard-key input{flex:1 1 260px}
  .yard button.compact{padding:6px 12px;font-size:13px}
  .yard-label{background:rgba(0,0,0,.6);color:#fff;border:0;border-radius:6px;box-shadow:none;font-size:12px;font-weight:600;padding:2px 6px;white-space:nowrap}
  .yard-label::before{display:none}
  .yard-vertex{width:14px;height:14px;margin:-7px 0 0 -7px;border-radius:50%;background:#fff;border:2px solid var(--blue);box-sizing:border-box}
  .yard-home{width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;background:var(--blue);border:2px solid #fff}
  .yard-pop{font-family:inherit;min-width:180px}
  .yard-pop b{font-size:14px}.yard-pop p{margin:4px 0;color:#555}
  .yard-pop .btns{display:flex;gap:6px;margin-top:8px}
  .yard-pop button{padding:6px 12px;font-size:13px}
  path.yard-watering{animation:yard-pulse 1.6s ease-in-out infinite}
  @keyframes yard-pulse{50%{fill-opacity:.85}}
  @media (prefers-reduced-motion:reduce){path.yard-watering{animation:none}}
  @media(max-width:600px){.yard-map{height:360px}}
`;

class YardMap {
  constructor(panel, entryId) {
    this.panel = panel;
    this.entryId = entryId;
    this.editing = false;
    this.drawing = null;
    this.shapes = new Map();
    this.vertices = [];
    this.root = document.createElement("section");
    this.root.className = "yard";
    this.root.innerHTML = `<link rel="stylesheet" href="${YARD_STATIC}/leaflet.css?v=1.9.4"><style>${YARD_STYLE}</style>
      <div class="yard-head"></div><div class="yard-key-slot"></div><div class="yard-tools"></div>
      <div class="yard-map" role="region" aria-label="Yard map"><div class="yard-apple" hidden></div><div class="yard-leaflet"></div></div><div class="yard-note yard-hint"></div>`;
    this.root.addEventListener("click", (event) => this._onClick(event));
    this.root.addEventListener("change", (event) => this._onChange(event));
    this.root.addEventListener("input", (event) => this._onInput(event));
    for (const type of ["dragenter", "dragover", "dragleave", "drop"]) {
      this.root.addEventListener(type, (event) => this._onDrag(event));
    }
    this._ticker = setInterval(() => this.refresh(), 30000);
  }

  get hass() {
    return this.panel._hass;
  }

  get imperial() {
    return this.hass?.config?.unit_system?.length === "mi";
  }

  _esc(value) {
    return this.panel._escape(value);
  }

  // Put the map in its slot after the panel redraws, and bring it up to date.
  mount(slot, controller) {
    this.controller = controller;
    if (this.root.parentElement !== slot) slot.appendChild(this.root);
    this._renderControls();
    if (this.map) {
      this.map.invalidateSize();
      this._syncLayers();
      return;
    }
    yardLoadLeaflet()
      .then((L) => {
        this.L = L;
        if (!this.map) this._createMap();
      })
      .catch((error) => {
        this.root.querySelector(".yard-leaflet").innerHTML = `<p class="yard-note" style="padding:16px">${this._esc(error.message)}</p>`;
      });
  }

  destroy() {
    clearInterval(this._ticker);
    this.apple?.destroy();
    this.map?.remove();
    this.root.remove();
  }

  get yard() {
    return this.controller?.yard || { background: "plain", esri_key: "", zones: {}, areas: {} };
  }

  get zones() {
    return this.controller?.zones || [];
  }

  _color(zone) {
    const index = this.zones.findIndex((z) => z.zone === zone);
    return YARD_COLORS[(index < 0 ? zone : index) % YARD_COLORS.length];
  }

  _createMap() {
    const L = this.L;
    const el = this.root.querySelector(".yard-leaflet");
    el.innerHTML = "";
    const home = [this.hass?.config?.latitude ?? 0, this.hass?.config?.longitude ?? 0];
    this.map = L.map(el, { zoomControl: true, maxZoom: 21, attributionControl: true });
    this.map.attributionControl.setPrefix(false);
    this.map.setView(home, 19);
    this.home = L.marker(home, { icon: L.divIcon({ className: "yard-home", iconSize: null }), interactive: false, keyboard: false });
    this.map.on("click", (event) => this._mapClick(event));
    this.map.on("move zoom resize zoomend moveend", () => this._syncApple());
    this.map.on("zoomanim", (event) => this._syncApple(event));
    this._syncLayers();
    const all = Object.values(this.yard.zones || {}).flat();
    if (all.length) this.map.fitBounds(L.latLngBounds(all), { padding: [24, 24], maxZoom: 20 });
    requestAnimationFrame(() => this.map?.invalidateSize());
  }

  // --- controls ------------------------------------------------------

  _renderControls() {
    const yard = this.yard;
    const busy = this._busy ? "disabled" : "";
    const head = `<h3>Yard</h3>
      <select data-yard-change="background" aria-label="Map background" ${busy}>
        ${Object.entries(YARD_BACKGROUNDS).map(([value, label]) => `<option value="${value}" ${yard.background === value ? "selected" : ""}>${this._esc(label)}</option>`).join("")}
      </select>
      ${this.editing
        ? `<button class="primary compact" data-yard-act="done" ${this.drawing ? "disabled" : ""}>Done</button>`
        : `<button class="secondary compact" data-yard-act="edit">${Object.keys(yard.zones || {}).length ? "Edit zones" : "Draw zones"}</button>`}`;
    this._put(".yard-head", head);

    const keyHtml = yard.background === "apple"
      ? this._appleKeyHtml(yard, busy)
      : yard.background === "esri"
      ? `<div class="yard-key"><input type="text" autocomplete="off" spellcheck="false" placeholder="ArcGIS API key" value="${this._esc(yard.esri_key || "")}" aria-label="ArcGIS API key">
          <button class="secondary compact" data-yard-act="save-key" ${busy}>Save key</button></div>
         ${yard.esri_key ? "" : '<p class="yard-note" style="margin:-4px 0 10px">Create a free API key with an ArcGIS Location Platform account, with access to basemaps, and paste it here.</p>'}`
      : "";
    this._put(".yard-key-slot", keyHtml);

    let tools = "";
    if (this.editing) {
      tools = this.zones.map((zone) => {
        const shape = yard.zones?.[String(zone.zone)];
        const drawingThis = this.drawing?.zone === zone.zone;
        const area = shape ? yardAreaText(yard.areas?.[String(zone.zone)] ?? yardAreaM2(shape), this.imperial) : "Not drawn yet";
        const buttons = drawingThis
          ? `<button class="primary compact" data-yard-act="finish" ${this.drawing.points.length >= 3 ? "" : "disabled"}>Finish (${this.drawing.points.length} points)</button>
             <button class="secondary compact" data-yard-act="undo" ${this.drawing.points.length ? "" : "disabled"}>Undo point</button>
             <button class="secondary compact" data-yard-act="cancel">Cancel</button>`
          : this.drawing
            ? ""
            : `<button class="secondary compact" data-yard-act="draw" data-zone="${zone.zone}" ${busy}>${shape ? "Redraw" : "Draw"}</button>
               ${shape ? `<button class="secondary compact" data-yard-act="remove" data-zone="${zone.zone}" ${busy}>Remove</button>` : ""}`;
        return `<div class="yard-zone ${drawingThis ? "active" : ""}"><span class="dot" style="background:${this._color(zone.zone)}"></span>
          <span class="name">${this._esc(zone.name)}</span><span class="muted">${this._esc(area)}</span>${buttons}</div>`;
      }).join("");
    }
    this._put(".yard-tools", tools);

    const note = this.drawing
      ? `Tap the map around the edge of ${this._esc(this._zoneName(this.drawing.zone))}, then Finish. Tap the first point to close the shape.`
      : this.editing
        ? "Drag a corner to move it. Shapes are saved as you go."
        : Object.keys(yard.zones || {}).length
          ? "Tap a zone to run or stop it."
          : "Draw each zone over your yard to see it light up while it waters.";
    this._put(".yard-hint", note);
    this.root.querySelector(".yard-map")?.classList.toggle("drawing", Boolean(this.drawing));
  }

  _appleKeyHtml(yard, busy) {
    if (yard.apple && !this._changingApple) {
      return `<div class="yard-key"><span class="muted" style="flex:1 1 auto">Apple MapKit key ${this._esc(yard.apple.key_id)} (team ${this._esc(yard.apple.team_id)})</span>
        <button class="secondary compact" data-yard-act="apple-change" ${busy}>Change key</button>
        <button class="secondary compact" data-yard-act="apple-clear" ${busy}>Remove key</button></div>`;
    }
    const draft = this._appleDraft();
    const file = draft.fileName
      ? `<b>${this._esc(draft.fileName)}</b><span>${draft.keyId ? `Key ID ${this._esc(draft.keyId)}` : "Add the Key ID below"}</span>`
      : `<b>Drop your AuthKey_….p8 file here</b><span>or tap to choose it</span>`;
    return `<div class="yard-apple-form">
      <p class="yard-note" style="margin:0">Drop the <b>.p8</b> file you downloaded for your MapKit JS key. The Key ID is read from its name. The private key stays in Home Assistant; the map only gets short-lived tokens signed with it.</p>
      <label class="yard-drop ${draft.pem ? "loaded" : ""}" data-yard-drop>
        <input type="file" accept=".p8,application/pkcs8,text/plain" data-yard-change="p8" hidden>
        ${file}
      </label>
      <div class="ids">
        <input type="text" name="team_id" value="${this._esc(draft.teamId)}" maxlength="10" placeholder="Team ID (10 characters)" autocomplete="off" spellcheck="false" aria-label="Apple Team ID" data-yard-input="teamId">
        ${draft.fileName && draft.keyId ? "" : `<input type="text" name="key_id" value="${this._esc(draft.keyId)}" maxlength="10" placeholder="Key ID" autocomplete="off" spellcheck="false" aria-label="MapKit Key ID" data-yard-input="keyId">`}
      </div>
      <p class="yard-note" style="margin:0">The Team ID isn't in the file: it's under <b>Membership details</b> on developer.apple.com. Once both are here the key saves by itself.</p>
      <details ${draft.pem && !draft.fileName ? "open" : ""}><summary class="yard-note">Paste the key instead</summary>
        <textarea name="private_key" placeholder="-----BEGIN PRIVATE KEY-----" spellcheck="false" aria-label="Private key (.p8 file contents)" data-yard-input="pem">${draft.fileName ? "" : this._esc(draft.pem)}</textarea>
      </details>
      <div class="ids"><button class="primary compact" data-yard-act="apple-save" ${busy || (this._appleReady() ? "" : "disabled")}>Save Apple key</button>
        ${yard.apple ? `<button class="secondary compact" data-yard-act="apple-cancel">Cancel</button>` : ""}</div>
    </div>`;
  }

  // What's been dropped or typed for the Apple key, kept across redraws.
  _appleDraft() {
    if (!this._apple) this._apple = { teamId: this.yard.apple?.team_id || "", keyId: "", pem: "", fileName: "" };
    return this._apple;
  }

  _appleReady() {
    const draft = this._appleDraft();
    const id = /^[A-Z0-9]{10}$/;
    return id.test(draft.teamId.trim().toUpperCase()) && id.test(draft.keyId.trim().toUpperCase()) && /BEGIN PRIVATE KEY/.test(draft.pem);
  }

  async _readP8(file) {
    if (!file) return;
    const draft = this._appleDraft();
    let text = "";
    try {
      text = await file.text();
    } catch (_error) {
      text = "";
    }
    const key = yardP8Key(file.name, text);
    if (!key) {
      this.panel._showNotice(`${file.name} isn't a .p8 private key.`, true);
      return;
    }
    draft.pem = key.pem;
    draft.fileName = file.name;
    if (key.keyId) draft.keyId = key.keyId;
    this._renderControls();
    if (this._appleReady()) this._saveApple();
    else this.root.querySelector("[name=team_id]")?.focus();
  }

  async _saveApple() {
    const draft = this._appleDraft();
    this._busy = true;
    this._renderControls();
    try {
      const result = await this.hass.callWS({
        type: `${DOMAIN}/panel/yard/apple_key`,
        team_id: draft.teamId.trim(),
        key_id: draft.keyId.trim(),
        private_key: draft.pem,
      });
      this.controller.yard.apple = result.apple;
      this._changingApple = false;
      this._apple = undefined;
      this.apple?.destroy();
      this.apple = undefined;
      this.panel._showNotice("Apple Maps key saved.");
    } catch (error) {
      this.panel._showNotice(error?.message || "Unable to save the Apple key.", true);
    }
    this._busy = false;
    this._renderControls();
    this._syncLayers();
  }

  // The Apple map under Leaflet, matched to Leaflet's view.
  async _syncAppleBackground() {
    const yard = this.yard;
    const wanted = yard.background === "apple" && Boolean(yard.apple);
    const el = this.root.querySelector(".yard-apple");
    const leaflet = this.root.querySelector(".yard-leaflet");
    leaflet?.classList.toggle("over-apple", wanted);
    if (!wanted) {
      this.apple?.destroy();
      this.apple = undefined;
      if (el) el.hidden = true;
      return;
    }
    el.hidden = false;
    if (this.apple || this._appleLoading) return;
    this._appleLoading = true;
    try {
      const mapkit = await yardLoadMapKit(this.hass, this.panel);
      if (this.yard.background !== "apple") return;
      this.mapkit = mapkit;
      this.apple = new mapkit.Map(el, {
        mapType: mapkit.Map.MapTypes.Satellite,
        showsCompass: mapkit.FeatureVisibility.Hidden,
        showsScale: mapkit.FeatureVisibility.Hidden,
        showsZoomControl: false,
        showsMapTypeControl: false,
        showsUserLocationControl: false,
        isRotationEnabled: false,
        isScrollEnabled: false,
        isZoomEnabled: false,
      });
      this._syncApple();
    } catch (error) {
      this.panel._showNotice(error?.message || "Apple Maps couldn't load.", true);
    } finally {
      this._appleLoading = false;
    }
  }

  _syncApple(zoomAnim) {
    if (!this.apple || !this.map || !this.mapkit) return;
    let rect;
    if (zoomAnim) {
      const center = this.map.project(zoomAnim.center, zoomAnim.zoom);
      const half = this.map.getSize().divideBy(2);
      rect = yardMapRect(center.x - half.x, center.y - half.y, center.x + half.x, center.y + half.y, zoomAnim.zoom);
    } else {
      const bounds = this.map.getPixelBounds();
      rect = yardMapRect(bounds.min.x, bounds.min.y, bounds.max.x, bounds.max.y, this.map.getZoom());
    }
    const mapRect = new this.mapkit.MapRect(...rect);
    if (zoomAnim) this.apple.setVisibleMapRectAnimated(mapRect, true);
    else this.apple.visibleMapRect = mapRect;
  }

  _put(selector, html) {
    const el = this.root.querySelector(selector);
    if (el && el.innerHTML !== html) el.innerHTML = html;
  }

  _zoneName(zone) {
    return this.zones.find((z) => z.zone === zone)?.name || `Zone ${zone}`;
  }

  // --- layers --------------------------------------------------------

  _syncLayers() {
    if (!this.map) return;
    const L = this.L;
    const yard = this.yard;
    const tileKey = `${yard.background}|${yard.esri_key || ""}`;
    if (tileKey !== this._tileKey) {
      this._tileKey = tileKey;
      this.tiles?.remove();
      this.tiles = undefined;
      const tiles = yardTiles(yard.background, yard.esri_key);
      if (tiles) {
        this.tiles = L.tileLayer(tiles.url, { maxNativeZoom: tiles.maxNativeZoom, maxZoom: 21, attribution: tiles.attribution }).addTo(this.map);
      }
      if (tiles || yard.background === "apple") this.home.remove();
      else this.home.addTo(this.map);
    }
    for (const [zone, layer] of this.shapes) {
      if (!yard.zones?.[String(zone)] || !this.zones.some((z) => z.zone === zone)) {
        layer.remove();
        this.shapes.delete(zone);
      }
    }
    const shown = new Set(this.zones.map((z) => String(z.zone)));
    for (const [key, points] of Object.entries(yard.zones || {})) {
      const zone = Number(key);
      if (!shown.has(key)) continue;
      let layer = this.shapes.get(zone);
      if (!layer) {
        layer = L.polygon(points, { weight: 2 }).addTo(this.map);
        layer.on("click", (event) => this._shapeClick(zone, event));
        this.shapes.set(zone, layer);
      } else {
        layer.setLatLngs(points);
      }
    }
    this._syncVertices();
    this._syncAppleBackground();
    this.refresh();
  }

  _syncVertices() {
    this.vertices.forEach((marker) => marker.remove());
    this.vertices = [];
    if (!this.map || !this.editing || this.drawing) return;
    const L = this.L;
    for (const [key, points] of Object.entries(this.yard.zones || {})) {
      if (!this.zones.some((z) => String(z.zone) === key)) continue;
      points.forEach((point, index) => {
        const marker = L.marker(point, {
          draggable: true,
          icon: L.divIcon({ className: "yard-vertex", iconSize: null }),
          title: `${this._zoneName(Number(key))}, corner ${index + 1}`,
        }).addTo(this.map);
        marker.on("drag", () => {
          const moved = this.yard.zones[key].map((p, i) => (i === index ? [marker.getLatLng().lat, marker.getLatLng().lng] : p));
          this.shapes.get(Number(key))?.setLatLngs(moved);
        });
        marker.on("dragend", () => {
          const shape = this.yard.zones[key].map((p, i) => (i === index ? [marker.getLatLng().lat, marker.getLatLng().lng] : p));
          this._save({ zone: Number(key), shape });
        });
        this.vertices.push(marker);
      });
    }
  }

  // Colors, labels and the watering pulse, from the live states.
  refresh() {
    if (!this.map) return;
    const states = this.hass?.states || {};
    for (const [zone, layer] of this.shapes) {
      const info = this.zones.find((z) => z.zone === zone);
      const valve = info?.valve_entity ? states[info.valve_entity]?.state : undefined;
      const watering = valve === "open" || valve === "opening";
      const left = watering ? yardMinutesLeft(states[info?.time_remaining_entity]?.state) : null;
      const color = watering ? YARD_WATERING : this._color(zone);
      const key = `${color}|${left}|${info?.name}|${this.editing}`;
      if (layer._yardKey === key) continue;
      layer._yardKey = key;
      layer.setStyle({ color: watering ? "#fff" : color, fillColor: color, fillOpacity: watering ? 0.6 : 0.3, weight: watering ? 3 : 2 });
      layer.getElement?.()?.classList.toggle("yard-watering", watering);
      const label = `${info?.name || `Zone ${zone}`}${watering ? ` · ${left ? `${left} min left` : "watering"}` : ""}`;
      if (layer.getTooltip()) layer.setTooltipContent(this._esc(label));
      else layer.bindTooltip(this._esc(label), { permanent: true, direction: "center", className: "yard-label" });
    }
  }

  // --- drawing -------------------------------------------------------

  _mapClick(event) {
    if (!this.drawing) return;
    const points = this.drawing.points;
    const latlng = [event.latlng.lat, event.latlng.lng];
    if (points.length >= 3) {
      const first = this.map.latLngToContainerPoint(points[0]);
      if (first.distanceTo(event.containerPoint) < 14) {
        this._finish();
        return;
      }
    }
    points.push(latlng);
    this._drawPreview();
    this._renderControls();
  }

  _drawPreview() {
    const L = this.L;
    this.drawing.layers.forEach((layer) => layer.remove());
    const color = this._color(this.drawing.zone);
    const points = this.drawing.points;
    this.drawing.layers = [
      L.polyline(points, { color, weight: 3, dashArray: "6 6" }).addTo(this.map),
      ...points.map((point, i) => L.circleMarker(point, { radius: i === 0 ? 7 : 5, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 }).addTo(this.map)),
    ];
  }

  _startDrawing(zone) {
    this.drawing = { zone, points: [], layers: [] };
    this.map?.doubleClickZoom.disable();
    this._syncVertices();
    this._renderControls();
  }

  _stopDrawing() {
    this.drawing?.layers.forEach((layer) => layer.remove());
    this.drawing = null;
    this.map?.doubleClickZoom.enable();
    this._syncVertices();
    this._renderControls();
  }

  async _finish() {
    if (!this.drawing || this.drawing.points.length < 3) return;
    const { zone, points } = this.drawing;
    this._stopDrawing();
    await this._save({ zone, shape: points });
  }

  // --- saving and actions --------------------------------------------

  async _save(change) {
    this._busy = true;
    this._renderControls();
    try {
      const yard = await this.hass.callWS({ type: `${DOMAIN}/panel/yard`, entry_id: this.entryId, ...change });
      this.controller.yard = yard;
    } catch (error) {
      this.panel._showNotice(error?.message || "Unable to save the yard map.", true);
    }
    this._busy = false;
    this._renderControls();
    this._syncLayers();
  }

  _shapeClick(zone, event) {
    if (this.editing || this.drawing) return;
    const L = this.L;
    const info = this.zones.find((z) => z.zone === zone);
    if (!info) return;
    const states = this.hass?.states || {};
    const valve = info.valve_entity ? states[info.valve_entity]?.state : undefined;
    const watering = valve === "open" || valve === "opening";
    const left = watering ? yardMinutesLeft(states[info.time_remaining_entity]?.state) : null;
    const lastRun = Date.parse(states[info.last_run_entity]?.state || "");
    const area = yardAreaText(this.yard.areas?.[String(zone)], this.imperial);
    const html = `<div class="yard-pop"><b>${this._esc(info.name)}</b>
      ${area ? `<p>${this._esc(area)}</p>` : ""}
      <p>${watering ? `Watering${left ? `, ${left} min left` : ""}` : Number.isNaN(lastRun) ? "Idle" : `Last ran ${this._esc(new Date(lastRun).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }))}`}</p>
      ${info.valve_entity ? `<div class="btns">${watering
        ? `<button class="danger" data-yard-act="stop" data-entity="${this._esc(info.valve_entity)}">Stop watering</button>`
        : `<button class="primary" data-yard-act="run" data-entity="${this._esc(info.valve_entity)}">Run now</button>`}</div>` : ""}
    </div>`;
    const popup = L.popup({ closeButton: true }).setLatLng(event.latlng).setContent(html).openOn(this.map);
    // Leaflet stops clicks inside a popup from reaching the page.
    popup.getElement()?.addEventListener("click", (click) => this._onClick(click));
  }

  async _onClick(event) {
    const button = event.target.closest?.("[data-yard-act]");
    if (!button || button.disabled) return;
    const act = button.dataset.yardAct;
    if (act === "edit") {
      this.editing = true;
      this.map?.closePopup();
      this._syncVertices();
      this._renderControls();
      this.refresh();
    } else if (act === "done") {
      this.editing = false;
      this._stopDrawing();
    } else if (act === "draw") {
      this._startDrawing(Number(button.dataset.zone));
    } else if (act === "finish") {
      this._finish();
    } else if (act === "undo") {
      this.drawing?.points.pop();
      this._drawPreview();
      this._renderControls();
    } else if (act === "cancel") {
      this._stopDrawing();
    } else if (act === "remove") {
      if (window.confirm?.(`Remove the outline of ${this._zoneName(Number(button.dataset.zone))}?`) === false) return;
      this._save({ zone: Number(button.dataset.zone), shape: null });
    } else if (act === "apple-save") {
      this._saveApple();
    } else if (act === "apple-change") {
      this._changingApple = true;
      this._renderControls();
    } else if (act === "apple-cancel") {
      this._changingApple = false;
      this._apple = undefined;
      this._renderControls();
    } else if (act === "apple-clear") {
      if (window.confirm?.("Remove the Apple MapKit key from Home Assistant?") === false) return;
      try {
        await this.hass.callWS({ type: `${DOMAIN}/panel/yard/apple_key`, clear: true });
        this.controller.yard.apple = null;
      } catch (error) {
        this.panel._showNotice(error?.message || "Unable to remove the Apple key.", true);
      }
      this._renderControls();
      this._syncLayers();
    } else if (act === "save-key") {
      const value = this.root.querySelector(".yard-key input")?.value || "";
      this._save({ esri_key: value });
    } else if (act === "run" || act === "stop") {
      this.map?.closePopup();
      try {
        await this.hass.callService("valve", act === "run" ? "open_valve" : "close_valve", { entity_id: button.dataset.entity });
      } catch (error) {
        this.panel._showNotice(error?.message || "Unable to change watering.", true);
      }
    }
  }

  _onChange(event) {
    if (event.target.dataset?.yardChange === "background") {
      this._save({ background: event.target.value });
    } else if (event.target.dataset?.yardChange === "p8") {
      this._readP8(event.target.files?.[0]);
      event.target.value = "";
    }
  }

  _onInput(event) {
    const key = event.target.dataset?.yardInput;
    if (!key) return;
    const draft = this._appleDraft();
    draft[key] = key === "pem" ? event.target.value : event.target.value.toUpperCase();
    if (key !== "pem") event.target.value = draft[key];
    if (key === "pem") draft.fileName = "";
    const save = this.root.querySelector('[data-yard-act="apple-save"]');
    if (save) save.disabled = this._busy || !this._appleReady();
    // A dropped file plus a full Team ID is everything: save without a click.
    if (key === "teamId" && draft.fileName && this._appleReady() && !this._busy) this._saveApple();
  }

  _onDrag(event) {
    const zone = event.target.closest?.("[data-yard-drop]");
    if (!zone) return;
    event.preventDefault();
    zone.classList.toggle("over", event.type === "dragover" || event.type === "dragenter");
    if (event.type === "drop") this._readP8(event.dataTransfer?.files?.[0]);
  }
}


class RainbirdExtendedPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._load();
    } else if (this._powerStatesChanged()) {
      // Turning the controller off or on changes the programs' next runs.
      this._refresh().catch(() => this._updateControllerStatuses());
    } else {
      this._updateControllerStatuses();
      this._yards?.forEach((yard) => yard.refresh());
    }
  }

  // Whether any controller's power entity changed state since the last render.
  _powerStatesChanged() {
    const states = this._hass?.states || {};
    const current = (this._data?.controllers || [])
      .map((controller) => (controller.power_entity ? states[controller.power_entity]?.state : ""))
      .join("|");
    const changed = this._powerStates !== undefined && current !== this._powerStates;
    this._powerStates = current;
    return changed;
  }

  // Whether a controller is turned off, from its Controller power entity.
  _turnedOff(controller) {
    if (!controller.power_entity) return false;
    return this._hass?.states?.[controller.power_entity]?.state === "off";
  }

  async _load() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    this._renderLoading();
    try {
      await Promise.all([
        this._hass.loadFragmentTranslation("config"),
        this._hass.loadBackendTranslation("options", DOMAIN),
        this._hass.loadBackendTranslation("selector", DOMAIN),
      ]);
      await this._refresh();
    } catch (error) {
      this._renderError(error);
    }
  }

  async _refresh() {
    this._setRefreshing(true);
    try {
      this._data = await this._hass.callWS({
        type: `${DOMAIN}/panel/config`,
      });
      this._powerStatesChanged();
    } finally {
      this._setRefreshing(false);
    }
    this._render();
  }

  _renderLoading() {
    this.shadowRoot.innerHTML = `${this._styles()}<main aria-busy="true">
      ${this._header()}
      <span class="visually-hidden" role="status">Loading Rain Bird Extended…</span>
      <div class="controller-list" aria-hidden="true">${this._skeletonCard()}</div>
    </main>`;
  }

  // Placeholder shaped like a controller card while the dashboard loads.
  _skeletonCard() {
    const boxes = (count, className) =>
      Array.from({ length: count }, () => `<div class="skeleton ${className}"></div>`).join("");
    return `<section class="controller skeleton-card">
      <div class="skeleton line eyebrow-line"></div>
      <div class="skeleton line title-line"></div>
      <div class="skeleton line short-line"></div>
      <div class="skeleton-row">${boxes(2, "pill")}</div>
      <div class="skeleton-row">${boxes(2, "button-shape")}</div>
      <div class="skeleton line heading-line"></div>
      <div class="skeleton-grid forecast-skeleton">${boxes(4, "box")}</div>
      <div class="skeleton line heading-line"></div>
      <div class="skeleton-grid">${boxes(3, "box small-box")}</div>
      <div class="skeleton line heading-line"></div>
      <div class="skeleton-grid">${boxes(2, "box")}</div>
    </section>`;
  }

  _header() {
    return `<header>
          <div>
            <h1>Rain Bird Extended</h1>
            <p>Controller settings, programs, and per-zone configuration.</p>
          </div>
          ${this._refreshButton()}
        </header>`;
  }

  _refreshButton() {
    if (this._refreshing && !this._data) {
      // First load: the skeleton already shows it's loading.
      return `<button class="secondary" data-action="refresh" disabled>Refresh</button>`;
    }
    return this._refreshing
      ? `<button class="secondary" data-action="refresh" disabled aria-busy="true"><span class="spinner" aria-hidden="true"></span>Refreshing…</button>`
      : `<button class="secondary" data-action="refresh">Refresh</button>`;
  }

  // Show the refresh in progress on the button, keeping the page as it is.
  _setRefreshing(refreshing) {
    this._refreshing = refreshing;
    const button = this.shadowRoot?.querySelector('[data-action="refresh"]');
    if (button) button.outerHTML = this._refreshButton();
  }

  _renderError(error) {
    this.shadowRoot.innerHTML = `${this._styles()}<main><section class="error">${this._escape(error?.message || "Unable to load the Rain Bird Extended dashboard.")}</section></main>`;
  }

  _render() {
    const controllers = this._data?.controllers || [];
    this.shadowRoot.innerHTML = `${this._styles()}
      <main>
        ${this._header()}
        ${
          controllers.length
            ? `<div class="controller-list">${controllers.map((controller) => this._controllerCard(controller)).join("")}</div>`
            : `<section class="empty"><h2>No controllers configured</h2><p>Add a Rain Bird controller in Home Assistant, then set up Rain Bird Extended from Devices &amp; services.</p></section>`
        }
        ${this._flow ? this._flowDialog() : ""}
        ${this._pendingStop ? this._stopConfirmation() : ""}
      </main>`;
    this._mountYards(controllers);
    this.shadowRoot.addEventListener("click", this._onRefreshClick ||= (event) => {
      // Delegated, because the button is swapped while refreshing.
      if (!event.target.closest?.('[data-action="refresh"]:not([disabled])')) return;
      this._refresh().catch((error) => this._showNotice(error?.message || "Unable to refresh the dashboard.", true));
    });
    this.shadowRoot.querySelectorAll('[data-action="configure"]').forEach((button) => {
      button.addEventListener("click", () => this._openOptions(button.dataset.entryId));
    });
    this.shadowRoot.querySelectorAll('[data-action="request-stop"]').forEach((button) => {
      button.addEventListener("click", () => {
        this._pendingStop = {
          action: button.dataset.stopAction,
          entityId: button.dataset.buttonEntityId,
          powerEntityId: button.dataset.powerEntityId,
        };
        this._render();
      });
    });
    this.shadowRoot.querySelectorAll('[data-action="confirm-stop"]').forEach((button) => {
      button.addEventListener("click", () => this._stopIrrigation());
    });
    this.shadowRoot.querySelectorAll('[data-action="cancel-stop"]').forEach((button) => {
      button.addEventListener("click", () => {
        this._pendingStop = undefined;
        this._render();
      });
    });
    this.shadowRoot.querySelectorAll('[data-action="hide-zone"]').forEach((button) => {
      button.addEventListener("click", () => {
        this._pendingHide = `${button.dataset.entryId}:${button.dataset.zone}`;
        this._render();
      });
    });
    this.shadowRoot.querySelectorAll('[data-action="cancel-hide-zone"]').forEach((button) => {
      button.addEventListener("click", () => {
        this._pendingHide = undefined;
        this._render();
      });
    });
    this.shadowRoot.querySelectorAll('[data-action="confirm-hide-zone"]').forEach((button) => {
      button.addEventListener("click", () => this._setZoneHidden(button, true));
    });
    this.shadowRoot.querySelectorAll('[data-action="show-zone"]').forEach((button) => {
      button.addEventListener("click", () => this._setZoneHidden(button, false));
    });
    this.shadowRoot.querySelectorAll('[data-action="set-number"]').forEach((button) => {
      button.addEventListener("click", () => this._setNumber(button));
    });
    this.shadowRoot.querySelectorAll('[data-action="set-rain-delay"]').forEach((button) => {
      button.addEventListener("click", () => this._setRainDelay(button));
    });
    this.shadowRoot.querySelectorAll('[data-action="close-flow"]').forEach((button) => {
      button.addEventListener("click", () => this._closeFlow());
    });
    this.shadowRoot.querySelector('[data-action="submit-flow"]')?.addEventListener("click", () => this._submitFlow());
    this.shadowRoot.querySelector("#options-form")?.addEventListener("submit", (event) => {
      event.preventDefault();
      this._submitFlow();
    });
    this.shadowRoot.querySelectorAll("[data-range]").forEach((input) => {
      input.addEventListener("input", () => {
        const output = input.parentElement.querySelector("output");
        if (output) output.textContent = input.value;
      });
    });
    const frequency = Array.from(this.shadowRoot.querySelectorAll("[data-path]"))
      .find((input) => JSON.stringify(JSON.parse(input.dataset.path)) === '["frequency"]');
    if (frequency) {
      frequency.addEventListener("change", () => this._syncFrequencyFields(frequency.value));
      this._syncFrequencyFields(frequency.value);
    }
    this.shadowRoot.querySelectorAll("[data-next-step]").forEach((button) => {
      button.addEventListener("click", () => this._submitMenu(button.dataset.nextStep));
    });
    this.shadowRoot.querySelectorAll("[data-zone-order]").forEach((button) => {
      button.addEventListener("click", () => this._moveOption(button));
    });
  }

  // Yard maps live outside the redraw: each is moved into its new slot.
  _mountYards(controllers) {
    this._yards ||= new Map();
    const seen = new Set();
    for (const controller of controllers) {
      const slot = this.shadowRoot.querySelector(`[data-yard-slot="${CSS.escape(controller.entry_id)}"]`);
      if (!slot) continue;
      seen.add(controller.entry_id);
      let yard = this._yards.get(controller.entry_id);
      if (!yard) {
        yard = new YardMap(this, controller.entry_id);
        this._yards.set(controller.entry_id, yard);
      }
      yard.mount(slot, controller);
    }
    for (const [entryId, yard] of this._yards) {
      if (!seen.has(entryId)) {
        yard.destroy();
        this._yards.delete(entryId);
      }
    }
  }

  _controllerCard(controller) {
    const status = this._controllerStatus(controller);
    const zones = controller.zones || [];
    return `<section class="controller">
      <div class="controller-head">
        <div>
          <div class="eyebrow">RAIN BIRD CONTROLLER</div>
          <h2>${this._escape(controller.rainbird_title || controller.title)}</h2>
          <p>${zones.length} ${zones.length === 1 ? "zone" : "zones"} · ${controller.program_count} ${controller.program_count === 1 ? "program" : "programs"}</p>
        </div>
        <div class="status-group">
          <span class="status ${status.availabilityClass}" data-controller-status="availability" data-entry-id="${this._escape(controller.entry_id)}">${status.availability}</span>
          <span class="status ${status.irrigationClass}" data-controller-status="irrigation" data-entry-id="${this._escape(controller.entry_id)}">${status.irrigation}</span>
        </div>
      </div>
      <div class="controller-actions">
        <button class="primary" data-action="configure" data-entry-id="${this._escape(controller.entry_id)}">Configure integration</button>
        ${controller.stop_button ? `<button class="danger" data-action="request-stop" data-stop-action="${this._escape(this._stopAction(controller))}" data-button-entity-id="${this._escape(controller.stop_button)}" data-power-entity-id="${this._escape(controller.power_entity || "")}">${this._stopAction(controller) === STOP_ACTION_TURN_OFF ? "Stop and turn off" : "Stop irrigation"}</button>` : ""}
        <span class="muted">${this._escape(controller.title)} · ${this._escape(controller.state)}</span>
      </div>
      ${controller.stop_button ? `<p class="stop-note">${this._stopAction(controller) === STOP_ACTION_TURN_OFF ? "Stops watering and turns the controller off. Programs won't run until it's turned back on." : "Stops active watering only. Configured programs remain enabled."}</p>` : ""}
      ${this._weatherAndRainDelay(controller)}
      ${zones.length ? `<div class="yard-slot" data-yard-slot="${this._escape(controller.entry_id)}"></div>` : ""}
      ${this._programsSection(controller)}
      ${
        zones.length
          ? `<h3>Zone configuration</h3><div class="zones">${zones.map((zone) => this._zoneCard(zone, controller.entry_id)).join("")}</div>`
          : controller.hidden_zones?.length
            ? ""
            : `<p class="muted">Zone configuration entities are not available yet.</p>`
      }
      ${this._hiddenZones(controller)}
    </section>`;
  }

  _weatherAndRainDelay(controller) {
    if (!controller.weather_entity && !controller.rain_delay) return "";
    const forecast = controller.forecast || [];
    const forecastContent = !controller.weather_entity
      ? `<p class="muted">Select a weather forecast entity in integration settings to see the forecast.</p>`
      : controller.forecast_error
        ? `<p class="muted">${this._escape(controller.forecast_error)}</p>`
        : forecast.length
          ? `<p class="muted">Showing ${forecast.length} available days.</p><div class="forecast-days">${forecast.map((day) => this._forecastCard(day, controller.forecast_unit)).join("")}</div>`
          : `<p class="muted">No forecast days are available.</p>`;
    return `<section class="weather-section">
      <div class="weather-layout">
        <div class="forecast-panel">
          <h3>10-day forecast</h3>
          ${forecastContent}
        </div>
        ${
          controller.rain_delay
            ? `<div class="rain-delay-panel">
                <h3>Controller rain delay</h3>
                ${this._rainDelayControl(controller.rain_delay, controller.entry_id)}
                <p class="muted">Set how many days the controller skips watering. Program next-run dates update to reflect this delay.</p>
              </div>`
            : ""
        }
      </div>
    </section>`;
  }

  _forecastCard(day, temperatureUnit) {
    const condition = day.condition
      ? String(day.condition).replace(/[-_]/g, " ")
      : "Forecast";
    const chance = day.precipitation_probability == null
      ? "Rain chance unavailable"
      : `${this._escape(day.precipitation_probability)}% rain`;
    const temperatures = [];
    if (day.temperature != null) {
      temperatures.push(`High ${this._escape(day.temperature)}${this._escape(temperatureUnit || "")}`);
    }
    if (day.templow != null) {
      temperatures.push(`Low ${this._escape(day.templow)}${this._escape(temperatureUnit || "")}`);
    }
    return `<article class="forecast-day">
      <h4>${this._escape(this._formatForecastDate(day.date))}</h4>
      <p class="forecast-condition">${this._escape(condition)}</p>
      <p>${chance}</p>
      ${temperatures.length ? `<p>${temperatures.join(" · ")}</p>` : ""}
    </article>`;
  }

  _formatForecastDate(value) {
    if (!value) return "Date unavailable";
    const [year, month, day] = value.split("-").map(Number);
    return new Intl.DateTimeFormat(this._hass?.locale?.language, {
      weekday: "short",
      month: "short",
      day: "numeric",
    }).format(new Date(year, month - 1, day, 12));
  }

  _controllerStatus(controller) {
    const states = this._hass?.states || {};
    const available = Boolean(controller.rainbird_loaded) &&
      (controller.rainbird_status_entities || []).some((entityId) => {
        const state = states[entityId]?.state;
        return state && state !== "unknown" && state !== "unavailable";
      });
    const primaryIrrigationState = states[controller.irrigating_entity]?.state;
    const irrigationStates = ["on", "off"].includes(primaryIrrigationState)
      ? [primaryIrrigationState]
      : (controller.watering_entities || [])
          .map((entityId) => states[entityId]?.state)
          .filter((state) => ["on", "off", "open", "closed"].includes(state));
    const irrigationActive = irrigationStates.some(
      (state) => state === "on" || state === "open",
    );
    const irrigationKnown = irrigationStates.length > 0;
    const irrigation = !available || !irrigationKnown
      ? "Unknown"
      : irrigationActive
        ? "Irrigating"
        : this._turnedOff(controller)
          ? "Turned off"
          : "Idle";
    return {
      availability: available ? "Online" : "Unavailable",
      availabilityClass: available ? "connected" : "disconnected",
      irrigation,
      irrigationClass: irrigation === "Irrigating"
        ? "irrigating"
        : irrigation === "Idle"
          ? "idle"
          : irrigation === "Turned off"
            ? "turned-off"
            : "unknown",
    };
  }

  _updateControllerStatuses() {
    if (!this._data || !this.shadowRoot) return;
    const controllers = new Map(
      (this._data.controllers || []).map((controller) => [
        controller.entry_id,
        controller,
      ]),
    );
    this.shadowRoot.querySelectorAll("[data-controller-status]").forEach((element) => {
      const controller = controllers.get(element.dataset.entryId);
      if (!controller) return;
      const status = this._controllerStatus(controller);
      const irrigation = element.dataset.controllerStatus === "irrigation";
      element.textContent = irrigation ? status.irrigation : status.availability;
      element.className = `status ${irrigation ? status.irrigationClass : status.availabilityClass}`;
    });
  }

  // Only programs that are set up; no section at all when none are.
  _programsSection(controller) {
    const programs = (controller.programs || []).filter((program) => program.configured);
    if (!programs.length) return "";
    const turnedOff = this._turnedOff(controller);
    return `<div class="program-section"><h3>Programs</h3><div class="programs">${programs.map((program) => this._programCard(program, turnedOff)).join("")}</div></div>`;
  }

  _programCard(program, turnedOff = false) {
    const label = program.name
      ? `Program ${program.letter} · ${program.name}`
      : `Program ${program.letter}`;
    const status = !program.configured
      ? `<span class="program-state">Not configured</span>`
      : turnedOff
        ? `<span class="program-state turned-off">Turned off</span><span class="program-next">Won't run until the controller is turned back on</span>`
        : `<span class="program-state configured">Configured</span><span class="program-next">Next run: ${this._formatProgramDate(program.next_run)}</span>`;
    return `<article class="program">
      <h4>${this._escape(label)}</h4>
      <div class="program-details">${status}</div>
    </article>`;
  }

  _formatProgramDate(value) {
    if (!value) {
      return "No upcoming run";
    }
    const [year, month, day] = value.split("-").map(Number);
    return new Intl.DateTimeFormat(this._hass?.locale?.language, {
      dateStyle: "full",
    }).format(new Date(year, month - 1, day, 12));
  }

  // The Stop button's action; turning off needs a power switch to turn off.
  _stopAction(controller) {
    const domain = (controller.power_entity || "").split(".")[0];
    return controller.stop_action === STOP_ACTION_TURN_OFF && ["switch", "input_boolean"].includes(domain)
      ? STOP_ACTION_TURN_OFF
      : STOP_ACTION_STOP_IRRIGATION;
  }

  _stopConfirmation() {
    if (this._pendingStop?.action === STOP_ACTION_TURN_OFF) {
      return `<div class="backdrop"><section class="dialog">
      ${this._dialogHeader("Stop and turn off the controller?")}
      <p class="description">This stops active watering now and turns the controller off. Its programs won't run until you turn it back on.</p>
      <footer><button class="secondary" data-action="cancel-stop">Cancel</button><button class="danger" data-action="confirm-stop">Stop and turn off</button></footer>
    </section></div>`;
    }
    return `<div class="backdrop"><section class="dialog">
      ${this._dialogHeader("Stop irrigation?")}
      <p class="description">This stops active watering now. It does not disable the controller or change its configured programs; they may run at their next scheduled time.</p>
      <footer><button class="secondary" data-action="cancel-stop">Cancel</button><button class="danger" data-action="confirm-stop">Stop irrigation</button></footer>
    </section></div>`;
  }

  async _stopIrrigation() {
    const pending = this._pendingStop;
    if (!pending) return;
    this._pendingStop = undefined;
    this._render();
    try {
      const turnOff = pending.action === STOP_ACTION_TURN_OFF;
      if (!pending.entityId || (turnOff && !pending.powerEntityId)) {
        throw new Error("The configured stop action is not supported.");
      }
      await this._hass.callService("button", "press", {
        entity_id: pending.entityId,
      });
      if (turnOff) {
        await this._hass.callService("homeassistant", "turn_off", {
          entity_id: pending.powerEntityId,
        });
      }
      await this._refresh();
      this._showNotice(turnOff ? "Irrigation stopped and the controller turned off." : "Active irrigation stopped.");
    } catch (error) {
      this._showNotice(error?.message || "Unable to stop irrigation.", true);
    }
  }

  _zoneCard(zone, entryId = "") {
    const name = zone.name || `Zone ${zone.zone}`;
    const key = `${entryId}:${zone.zone}`;
    const hide = entryId
      ? `<button class="secondary compact" data-action="hide-zone" data-entry-id="${this._escape(entryId)}" data-zone="${zone.zone}" aria-label="Hide ${this._escape(name)}">Hide</button>`
      : "";
    const confirm = this._pendingHide === key
      ? `<div class="zone-hide"><p>Hide ${this._escape(name)}? Its Rain Bird Extended entities are turned off and it leaves this dashboard. Rain Bird's own switch for it isn't changed, so the controller's schedule can still run it.</p>
          <div class="zone-hide-actions"><button class="danger compact" data-action="confirm-hide-zone" data-entry-id="${this._escape(entryId)}" data-zone="${zone.zone}">Hide zone</button>
          <button class="secondary compact" data-action="cancel-hide-zone">Cancel</button></div></div>`
      : "";
    return `<article class="zone">
      <div class="zone-head"><h4>${this._escape(name)}</h4>${hide}</div>
      ${confirm}
      <div class="numbers">${zone.numbers.map((number) => this._numberControl(number, name)).join("")}</div>
    </article>`;
  }

  // Zones turned off from here, each with a way back.
  _hiddenZones(controller) {
    const hidden = controller.hidden_zones || [];
    if (!hidden.length) return "";
    return `<div class="hidden-zones"><h3>Hidden zones</h3><div class="hidden-zone-list">${hidden.map((zone) =>
      `<span class="hidden-zone">${this._escape(zone.name)}<button class="secondary compact" data-action="show-zone" data-entry-id="${this._escape(controller.entry_id)}" data-zone="${zone.zone}">Show</button></span>`).join("")}</div>
      <p class="muted">Hidden zones have their Rain Bird Extended entities turned off.</p></div>`;
  }

  async _setZoneHidden(button, hidden) {
    button.disabled = true;
    try {
      await this._hass.callWS({
        type: "rainbird_extended/panel/zone_hidden",
        entry_id: button.dataset.entryId,
        zone: Number(button.dataset.zone),
        hidden,
      });
      this._pendingHide = undefined;
      this._showNotice(hidden ? "Zone hidden." : "Zone shown. Its settings come back in a moment.");
      await this._refresh();
      // Showing a zone reloads Rain Bird Extended; ask again once it's back.
      if (!hidden) setTimeout(() => this._refresh().catch(() => {}), 4000);
    } catch (error) {
      button.disabled = false;
      this._showNotice(error?.message || "Unable to change the zone.", true);
    }
  }

  _numberControl(number, zoneName) {
    const settingName = zoneName && number.name.startsWith(`${zoneName} `)
      ? number.name.slice(zoneName.length + 1)
      : number.name;
    const name = this._escape(settingName);
    const unit = this._escape(number.unit || "");
    const value = number.value === "unknown" || number.value === "unavailable" ? "" : number.value;
    return `<label class="number-row">
      <span>${name}</span>
      <span class="number-input">
        <input type="number" value="${this._escape(value)}" min="${this._escape(number.min ?? "")}" max="${this._escape(number.max ?? "")}" step="${this._escape(number.step ?? "any")}" data-number-value="${this._escape(number.entity_id)}" aria-label="${name}">
        <small>${unit}</small>
        <button class="secondary compact" data-action="set-number" data-entity-id="${this._escape(number.entity_id)}">Save</button>
      </span>
    </label>`;
  }

  _rainDelayControl(number, entryId) {
    const name = this._escape(number.name);
    const unit = this._escape(number.unit || "");
    const value = number.value === "unknown" || number.value === "unavailable" ? "" : number.value;
    return `<label class="number-row">
      <span>${name}</span>
      <span class="number-input">
        <input type="number" value="${this._escape(value)}" min="${this._escape(number.min ?? 0)}" max="${this._escape(number.max ?? 14)}" step="${this._escape(number.step ?? 1)}" data-rain-delay-value="${this._escape(entryId)}" aria-label="${name}">
        <small>${unit}</small>
        <button class="secondary compact" data-action="set-rain-delay" data-entry-id="${this._escape(entryId)}">Save</button>
      </span>
    </label>`;
  }

  async _setNumber(button) {
    const input = this.shadowRoot.querySelector(`[data-number-value="${CSS.escape(button.dataset.entityId)}"]`);
    if (!input || input.value === "") {
      return;
    }
    button.disabled = true;
    try {
      await this._hass.callService("number", "set_value", {
        entity_id: button.dataset.entityId,
        value: Number(input.value),
      });
      await this._refresh();
    } catch (error) {
      this._showNotice(error?.message || "Unable to update this zone setting.", true);
    } finally {
      button.disabled = false;
    }
  }

  async _setRainDelay(button) {
    const input = this.shadowRoot.querySelector(
      `[data-rain-delay-value="${CSS.escape(button.dataset.entryId)}"]`,
    );
    if (!input || input.value === "") {
      return;
    }
    const days = Number(input.value);
    if (!Number.isInteger(days) || days < Number(input.min) || days > Number(input.max)) {
      this._showNotice("Enter a whole number within the rain delay range.", true);
      return;
    }
    button.disabled = true;
    try {
      await this._hass.callWS({
        type: `${DOMAIN}/panel/rain_delay`,
        entry_id: button.dataset.entryId,
        days,
      });
      await this._refresh();
      this._showNotice("Controller rain delay updated.");
    } catch (error) {
      this._showNotice(error?.message || "Unable to update the controller rain delay.", true);
    } finally {
      button.disabled = false;
    }
  }

  async _openOptions(entryId) {
    this._flow = { entryId, loading: true };
    this._render();
    try {
      const step = await this._hass.callApi(
        "POST",
        "config/config_entries/options/flow",
        { handler: entryId },
      );
      this._flow = { entryId, step, loading: false };
      this._render();
    } catch (error) {
      this._flow = undefined;
      this._render();
      this._showNotice(error?.message || "Unable to open integration settings.", true);
    }
  }

  _flowDialog() {
    const flow = this._flow;
    const step = flow.step;
    if (!step) {
      return `<div class="backdrop"><section class="dialog" aria-busy="true">
        <span class="visually-hidden" role="status">Opening configuration…</span>
        <div aria-hidden="true">
          <div class="skeleton line title-line"></div>
          <div class="skeleton line"></div>
          ${Array.from({ length: 4 }, () => '<div class="skeleton line short-line"></div><div class="skeleton field-shape"></div>').join("")}
        </div>
      </section></div>`;
    }
    if (step.type === "menu") {
      const options = (step.menu_options || []).map((option) => {
        const label = this._localize(`component.${DOMAIN}.options.step.${step.step_id}.menu_options.${option}`, option.replaceAll("_", " "));
        return `<button class="menu-option" data-next-step="${this._escape(option)}" ${flow.loading ? "disabled" : ""}>${this._escape(label)}<span>›</span></button>`;
      }).join("");
      return `<div class="backdrop"><section class="dialog" ${flow.loading ? 'aria-busy="true"' : ""}>
        ${this._dialogHeader(this._stepTitle(step))}
        <p class="description">${this._stepDescription(step)}</p>
        <div class="menu">${options}</div>
        <footer><button class="secondary" data-action="close-flow">Close</button></footer>
      </section></div>`;
    }
    if (step.type === "form") {
      const initial = this._flow.values || this._initialValues(step.data_schema || []);
      const fields = (step.data_schema || []).map((field) => this._renderField(field, [], initial)).join("");
      const errors = Object.entries(step.errors || {}).map(([key, error]) => {
        const message = this._localize(`component.${DOMAIN}.options.error.${error}`, error);
        return `<p class="field-error" data-error="${this._escape(key)}">${this._escape(message)}</p>`;
      }).join("");
      return `<div class="backdrop"><section class="dialog">
        ${this._dialogHeader(this._stepTitle(step))}
        <p class="description">${this._stepDescription(step)}</p>
        ${errors ? `<div class="errors">${errors}</div>` : ""}
        <form id="options-form">${fields}</form>
        <footer><button class="secondary" data-action="close-flow" ${flow.loading ? "disabled" : ""}>Cancel</button>${
          flow.loading
            ? `<button class="primary" data-action="submit-flow" disabled aria-busy="true"><span class="spinner" aria-hidden="true"></span>Saving…</button>`
            : `<button class="primary" data-action="submit-flow">Save</button>`
        }</footer>
      </section></div>`;
    }
    if (step.type === "create_entry") {
      return `<div class="backdrop"><section class="dialog">
        ${this._dialogHeader("Settings saved")}
        <p class="description">Rain Bird Extended has been updated.</p>
        <footer><button class="primary" data-action="close-flow">Done</button></footer>
      </section></div>`;
    }
    return `<div class="backdrop"><section class="dialog">
      ${this._dialogHeader("Configuration")}
      <p class="description">${this._escape(step.reason || step.type || "This configuration step is not available.")}</p>
      <footer><button class="secondary" data-action="close-flow">Close</button></footer>
    </section></div>`;
  }

  _dialogHeader(title) {
    return `<div class="dialog-head"><h2>${this._escape(title)}</h2><button class="icon-button" data-action="close-flow" aria-label="Close">×</button></div>`;
  }

  _stepTitle(step) {
    return this._localize(
      `component.${DOMAIN}.options.step.${step.step_id}.title`,
      "Settings",
      step.description_placeholders,
    );
  }

  _stepDescription(step) {
    const description = this._localize(
      `component.${DOMAIN}.options.step.${step.step_id}.description`,
      "",
      step.description_placeholders,
    );
    return this._escape(description);
  }

  _renderField(field, path, data) {
    const name = field.name;
    if (field.type === "expandable") {
      const sectionData = data?.[name] || {};
      const title = this._localize(`component.${DOMAIN}.options.step.${this._flow.step.step_id}.sections.${name}.name`, name.replaceAll("_", " "), this._flow.step.description_placeholders);
      return `<fieldset><legend>${this._escape(title)}</legend>${(field.schema || []).map((child) => this._renderField(child, [...path, name], sectionData)).join("")}</fieldset>`;
    }
    const inputPath = [...path, name];
    const pathValue = this._escape(JSON.stringify(inputPath));
    const label = this._localize(`component.${DOMAIN}.options.step.${this._flow.step.step_id}.${path.length ? `sections.${path[0]}.` : ""}data.${name}`, name.replaceAll("_", " "), this._flow.step.description_placeholders);
    const helper = this._localize(`component.${DOMAIN}.options.step.${this._flow.step.step_id}.${path.length ? `sections.${path[0]}.` : ""}data_description.${name}`, "", this._flow.step.description_placeholders);
    const selector = field.selector || {};
    const key = Object.keys(selector)[0];
    const config = selector[key] || {};
    const value = data?.[name] ?? field.default;
    const frequency = this._frequencyOnly(this._flow.step.step_id, name);
    const frequencyAttribute = frequency
      ? ` data-frequency-only="${frequency}"${data?.frequency !== frequency ? " hidden" : ""}`
      : "";
    return `<label class="field"${frequencyAttribute}>
      <span class="field-label">${this._escape(label)}${field.required ? " *" : ""}</span>
      ${this._renderControl(key, config, value, pathValue)}
      ${helper ? `<small>${this._escape(helper)}</small>` : ""}
    </label>`;
  }

  _frequencyOnly(stepId, fieldName) {
    if (!stepId?.startsWith("program_")) {
      return undefined;
    }
    if (fieldName === "days") {
      return "custom";
    }
    if (fieldName === "every_days" || fieldName === "start_date") {
      return "cyclic";
    }
    return undefined;
  }

  _syncFrequencyFields(frequency) {
    this.shadowRoot.querySelectorAll("[data-frequency-only]").forEach((field) => {
      field.hidden = field.dataset.frequencyOnly !== frequency;
    });
  }

  _renderControl(type, config, value, path) {
    if (type === "boolean" || type === undefined && typeof value === "boolean") {
      return `<input type="checkbox" data-path="${path}" ${value ? "checked" : ""}>`;
    }
    if (type === "number") {
      const number = config.number || config;
      const mode = number.mode === "slider" ? "range" : "number";
      const unit = number.unit_of_measurement
        ? `<small>${this._escape(number.unit_of_measurement)}</small>`
        : "";
      const output = mode === "range"
        ? `<output>${this._escape(value ?? "")}</output>`
        : "";
      return `<span class="number-control"><input type="${mode}" data-path="${path}" ${mode === "range" ? "data-range" : ""} value="${this._escape(value ?? "")}" min="${this._escape(number.min ?? "")}" max="${this._escape(number.max ?? "")}" step="${this._escape(number.step ?? "any")}">${unit}${output}</span>`;
    }
    if (type === "select") {
      const options = config.select?.options || config.options || [];
      const multiple = Boolean(config.select?.multiple ?? config.multiple);
      const translationKey = config.select?.translation_key ?? config.translation_key;
      const selected = Array.isArray(value) ? value.map(String) : [String(value ?? "")];
      const rendered = options.map((option) => {
        const optionValue = typeof option === "object" ? option.value : option;
        const optionLabel = typeof option === "object" ? option.label : option;
        const isSelected = selected.includes(String(optionValue));
        const translation = translationKey
          ? `component.${DOMAIN}.selector.${translationKey}.options.${optionValue}`
          : `component.${DOMAIN}.selector.${optionLabel}`;
        return `<option value="${this._escape(optionValue)}" ${isSelected ? "selected" : ""}>${this._escape(this._localize(translation, optionLabel))}</option>`;
      }).join("");
      return `<select data-path="${path}" ${multiple ? "multiple size=\"5\"" : ""}>${rendered}</select>`;
    }
    if (type === "entity") {
      const entityConfig = config.entity || config;
      const multiple = Boolean(entityConfig.multiple);
      const options = this._entityOptions(entityConfig, value);
      const selected = Array.isArray(value) ? value.map(String) : [String(value || "")];
      return `<select data-path="${path}" ${multiple ? 'multiple size="7"' : ""}>${options.map((entity) => `<option value="${this._escape(entity.entity_id)}" ${selected.includes(entity.entity_id) ? "selected" : ""}>${this._escape(entity.label)}</option>`).join("")}</select>${multiple && entityConfig.reorder ? `<div class="order-buttons"><button type="button" class="secondary compact" data-zone-order="up">Move up</button><button type="button" class="secondary compact" data-zone-order="down">Move down</button></div>` : ""}`;
    }
    if (type === "time") {
      return `<input type="time" step="1" data-path="${path}" value="${this._escape(value || "")}">`;
    }
    if (type === "date") {
      return `<input type="date" data-path="${path}" value="${this._escape(value || "")}">`;
    }
    if (type === "text" || type === "string" || type === undefined) {
      return `<input type="text" data-path="${path}" value="${this._escape(value ?? "")}">`;
    }
    return `<input type="text" data-path="${path}" value="${this._escape(value ?? "")}">`;
  }

  _entityOptions(config, currentValue) {
    const states = this._hass.states || {};
    const current = Array.isArray(currentValue) ? currentValue : [currentValue];
    const include = config.include_entities ? new Set(config.include_entities) : undefined;
    const exclude = new Set(config.exclude_entities || []);
    const domains = config.domain ? (Array.isArray(config.domain) ? config.domain : [config.domain]) : undefined;
    const selected = [];
    const available = [];
    Object.entries(states).forEach(([entityId, state]) => {
      if (include && !include.has(entityId) || exclude.has(entityId)) return;
      if (domains && !domains.includes(entityId.split(".")[0])) return;
      if (config.device_class && state.attributes?.device_class !== config.device_class) return;
      const item = { entity_id: entityId, label: state.attributes?.friendly_name || entityId };
      (current.includes(entityId) ? selected : available).push(item);
    });
    current.forEach((entityId) => {
      if (!entityId || selected.some((item) => item.entity_id === entityId)) return;
      if (include && !include.has(entityId) || exclude.has(entityId)) return;
      selected.push({ entity_id: entityId, label: entityId });
    });
    const byName = (a, b) => a.label.localeCompare(b.label);
    selected.sort((a, b) => current.indexOf(a.entity_id) - current.indexOf(b.entity_id));
    available.sort(byName);
    if (!config.multiple) {
      return [{ entity_id: "", label: "—" }, ...selected, ...available];
    }
    return [...selected, ...available];
  }

  _initialValues(schema) {
    const values = {};
    for (const field of schema) {
      if (field.type === "expandable") {
        values[field.name] = this._initialValues(field.schema || []);
      } else if (field.description?.suggested_value !== undefined && field.description?.suggested_value !== null) {
        values[field.name] = field.description.suggested_value;
      } else if (Object.hasOwn(field, "default")) {
        values[field.name] = field.default;
      } else if (field.required && field.selector?.boolean) {
        values[field.name] = false;
      }
    }
    return values;
  }

  async _submitMenu(nextStepId) {
    await this._advanceFlow({ next_step_id: nextStepId });
  }

  async _submitFlow() {
    const form = this.shadowRoot.querySelector("#options-form");
    if (!form) return;
    const data = {};
    for (const control of form.querySelectorAll("[data-path]")) {
      if (control.closest("[data-frequency-only][hidden]")) {
        continue;
      }
      const path = JSON.parse(control.dataset.path);
      let value;
      if (control.type === "checkbox") {
        value = control.checked;
      } else if (control instanceof HTMLSelectElement && control.multiple) {
        value = Array.from(control.selectedOptions, (option) => option.value);
      } else if (control.type === "number" || control.type === "range") {
        value = control.value === "" ? undefined : Number(control.value);
      } else {
        value = control.value;
      }
      let container = data;
      for (const key of path.slice(0, -1)) {
        container[key] ||= {};
        container = container[key];
      }
      if (value !== undefined && value !== "") {
        container[path[path.length - 1]] = value;
      }
    }
    this._flow.values = data;
    await this._advanceFlow(data);
  }

  async _advanceFlow(data) {
    const flow = this._flow;
    if (!flow?.step?.flow_id) return;
    const previousStepId = flow.step.step_id;
    flow.loading = true;
    this._render();
    try {
      const step = await this._hass.callApi(
        "POST",
        `config/config_entries/options/flow/${flow.step.flow_id}`,
        data,
      );
      if (step.type === "create_entry") {
        flow.step = step;
        flow.loading = false;
        flow.values = undefined;
      } else {
        flow.step = step;
        flow.loading = false;
        flow.values = step.type === "form" && step.step_id === previousStepId
          ? data
          : undefined;
      }
      this._render();
    } catch (error) {
      flow.loading = false;
      this._render();
      this._showNotice(error?.message || "Unable to save these settings.", true);
    }
  }

  async _closeFlow() {
    const flow = this._flow;
    this._flow = undefined;
    this._render();
    if (flow?.step?.flow_id && flow.step.type !== "create_entry") {
      try {
        await this._hass.callApi("DELETE", `config/config_entries/options/flow/${flow.step.flow_id}`);
      } catch (error) {
        this._showNotice(error?.message || "Unable to close the configuration flow.", true);
      }
    }
    try {
      await this._refresh();
    } catch (error) {
      this._showNotice(error?.message || "Unable to refresh the dashboard.", true);
    }
  }

  _moveOption(button) {
    const select = button.closest(".field")?.querySelector("select[multiple]");
    if (!select) return;
    const selected = Array.from(select.selectedOptions);
    if (button.dataset.zoneOrder === "up") {
      selected.forEach((option) => {
        const previous = option.previousElementSibling;
        if (previous && !previous.selected) select.insertBefore(option, previous);
      });
    } else {
      selected.reverse().forEach((option) => {
        const next = option.nextElementSibling;
        if (next && !next.selected) select.insertBefore(next, option);
      });
    }
  }

  _localize(key, fallback, placeholders) {
    return this._hass.localize(key, placeholders) || fallback;
  }

  _showNotice(message, isError = false) {
    const notice = document.createElement("div");
    notice.className = `notice ${isError ? "error" : ""}`;
    notice.textContent = message;
    this.shadowRoot.querySelector("main")?.prepend(notice);
    setTimeout(() => notice.remove(), 5000);
  }

  _escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  _styles() {
    return `<style>
      :host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,Noto,sans-serif);--blue:var(--primary-color,#03a9f4);--border:var(--divider-color,#e8e8e8)}
      *{box-sizing:border-box}main{max-width:1120px;margin:0 auto;padding:32px 24px 56px}
      header,.controller-head,.controller-actions,.dialog-head,footer{display:flex;align-items:center;justify-content:space-between;gap:16px}
      header{margin-bottom:24px}h1{font-size:28px;margin:0 0 6px}h2{font-size:20px;margin:0 0 4px}h3{font-size:16px;margin:22px 0 12px}h4{margin:0 0 12px}p{color:var(--secondary-text-color);margin:0}
      .controller-list{display:grid;gap:18px}.controller,.empty{background:var(--card-background-color,#fff);border:1px solid var(--border);border-radius:14px;padding:22px;box-shadow:var(--ha-card-box-shadow,0 1px 3px #0001)}
      .controller-head{align-items:flex-start}.eyebrow{font-size:11px;letter-spacing:.08em;font-weight:600;color:var(--secondary-text-color);margin-bottom:7px}
      .status-group{display:flex;flex-wrap:wrap;gap:8px}.status{border-radius:20px;padding:6px 11px;font-size:12px;font-weight:600;white-space:nowrap}.connected,.idle{background:var(--success-color,#2e7d32);color:var(--text-primary-color,#fff)}.disconnected,.irrigating{background:var(--warning-color,#ed6c02);color:var(--text-primary-color,#fff)}.unknown{background:var(--secondary-background-color,#f1f3f4);color:var(--secondary-text-color)}.turned-off{background:var(--disabled-text-color,#757575);color:var(--text-primary-color,#fff)}
      .controller-actions{justify-content:flex-start;margin-top:17px;flex-wrap:wrap}.muted{font-size:13px;color:var(--secondary-text-color)}.stop-note{font-size:12px;margin-top:8px}
      .danger{background:var(--error-color,#db4437);color:var(--text-primary-color,#fff);font-weight:600}
      .weather-section{margin-top:22px}.weather-layout{display:grid;grid-template-columns:minmax(0,3fr) minmax(230px,1fr);gap:16px}.forecast-panel,.rain-delay-panel{border:1px solid var(--border);border-radius:10px;padding:16px}.weather-section h3{margin:0 0 12px}.forecast-days{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}.forecast-day{border:1px solid var(--border);border-radius:8px;padding:10px}.forecast-day h4{margin:0 0 6px}.forecast-day p{font-size:12px;margin-top:4px}.forecast-condition{text-transform:capitalize}.rain-delay-panel .number-row{margin:8px 0 12px}.rain-delay-panel .muted{line-height:1.5}
      .program-section{margin-top:22px}.program-section h3{margin:0 0 12px}.programs{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.program{border:1px solid var(--border);border-radius:10px;padding:13px 14px}.program h4{margin:0 0 9px}.program-details{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.program-state{display:inline-flex;border-radius:16px;padding:4px 9px;font-size:12px;font-weight:600;background:var(--secondary-background-color,#f1f3f4);color:var(--secondary-text-color)}.program-state.configured{background:var(--success-color,#2e7d32);color:var(--text-primary-color,#fff)}.program-state.turned-off{background:var(--disabled-text-color,#757575);color:var(--text-primary-color,#fff)}.program-next{font-size:13px;color:var(--secondary-text-color)}
      button{font:inherit;border:0;border-radius:8px;padding:10px 16px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.primary{background:var(--blue);color:var(--text-primary-color,#fff);font-weight:600}.secondary{background:var(--secondary-background-color,#f1f3f4);color:var(--primary-text-color)}.compact{padding:6px 10px;font-size:12px}
      .zones{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:12px}.zone{border:1px solid var(--border);border-radius:10px;padding:14px}.zone-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.zone-head h4{margin:0}.zone-hide{margin:10px 0;padding:10px;border-radius:8px;background:var(--secondary-background-color,#f1f3f4);font-size:13px}.zone-hide p{margin:0 0 8px}.zone-hide-actions{display:flex;gap:8px}.hidden-zones{margin-top:18px}.hidden-zones h3{margin:0 0 10px}.hidden-zone-list{display:flex;flex-wrap:wrap;gap:8px}.hidden-zone{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--border);border-radius:18px;padding:4px 4px 4px 12px;font-size:13px}.number-row{display:grid;gap:6px;margin:12px 0;font-size:13px}.number-input,.number-control{display:flex;align-items:center;gap:7px}.number-input input,.number-control input{min-width:0;flex:1}.number-input small,.number-control small,.number-control output{color:var(--secondary-text-color)}input,select{font:inherit;color:var(--primary-text-color);background:var(--input-fill-color,var(--card-background-color,#fff));border:1px solid var(--border);border-radius:7px;padding:9px;min-width:0;width:100%}input[type=range],input[type=checkbox]{width:auto}
      .controller{min-width:0}.empty{text-align:center;padding:48px 22px}.empty h2{margin-bottom:8px}
      .visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
      .skeleton{--skeleton-base:color-mix(in srgb,var(--primary-text-color,#000) 8%,transparent);--skeleton-shine:color-mix(in srgb,var(--primary-text-color,#000) 14%,transparent);background:linear-gradient(90deg,var(--skeleton-base) 0%,var(--skeleton-shine) 50%,var(--skeleton-base) 100%);background-size:200% 100%;border-radius:8px;animation:rbx-shimmer 1.4s ease-in-out infinite}
      .skeleton.line{height:12px;margin:0 0 12px;width:70%}.skeleton.eyebrow-line{width:140px;height:10px}.skeleton.title-line{width:55%;height:22px}.skeleton.short-line{width:30%}.skeleton.heading-line{width:120px;height:14px;margin-top:22px}
      .skeleton-row{display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 14px}.skeleton.pill{width:72px;height:28px;border-radius:20px}.skeleton.button-shape{width:170px;height:40px}
      .skeleton-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.skeleton.box{height:96px;border-radius:10px}.skeleton.small-box{height:64px}.skeleton.field-shape{height:38px;margin:0 0 16px}
      button[aria-busy=true]{display:inline-flex;align-items:center;white-space:nowrap}.spinner{display:inline-block;flex:none;width:14px;height:14px;margin-right:8px;vertical-align:-2px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:rbx-spin .8s linear infinite}
      @keyframes rbx-shimmer{from{background-position:100% 0}to{background-position:-100% 0}}@keyframes rbx-spin{to{transform:rotate(360deg)}}
      @media (prefers-reduced-motion:reduce){.skeleton{animation:none}.spinner{animation:none;border-right-color:currentColor;opacity:.6}}
      .backdrop{position:fixed;inset:0;background:#0008;display:grid;place-items:center;padding:20px;z-index:10}.dialog{width:min(760px,100%);max-height:90vh;overflow:auto;background:var(--card-background-color,#fff);border-radius:16px;padding:24px;box-shadow:0 16px 48px #0004}.dialog-head{margin-bottom:12px}.icon-button{font-size:24px;padding:2px 8px;background:transparent;color:var(--primary-text-color)}.description{margin:0 0 18px;line-height:1.5;white-space:pre-wrap}
      .menu{display:grid;gap:8px}.menu-option{display:flex;justify-content:space-between;align-items:center;text-align:left;background:var(--secondary-background-color,#f1f3f4);color:var(--primary-text-color)}.menu-option span{font-size:22px;color:var(--secondary-text-color)}
      form{display:grid;gap:14px}.field{display:grid;gap:7px}.field[hidden]{display:none}.field-label{font-weight:500}.field small{color:var(--secondary-text-color);line-height:1.4}.field-error{color:var(--error-color,#db4437);font-size:13px}fieldset{border:1px solid var(--border);border-radius:10px;padding:14px;display:grid;gap:14px}legend{font-weight:600;padding:0 7px}.order-buttons{display:flex;gap:8px;margin-top:8px}
      footer{justify-content:flex-end;margin-top:22px;padding-top:16px;border-top:1px solid var(--border)}.notice{padding:12px 14px;margin:0 0 14px;border-radius:8px;background:var(--success-color,#2e7d32);color:#fff}.error{background:var(--error-color,#db4437);color:#fff}
      @media(max-width:700px){.weather-layout{grid-template-columns:1fr}}@media(max-width:600px){main{padding:18px 12px 36px}header{align-items:flex-start}h1{font-size:23px}.controller{padding:16px}.controller-head{flex-direction:column}.controller-actions{align-items:flex-start;flex-direction:column}.dialog{padding:18px}.zones{grid-template-columns:1fr}}
    </style>`;
  }
}

// The yard map's pure helpers, for the tests.
RainbirdExtendedPanel.yard = { yardAreaM2, yardAreaText, yardMinutesLeft, yardTiles, yardMapRect, yardP8Key, YardMap };

customElements.define("rainbird-extended-panel", RainbirdExtendedPanel);
