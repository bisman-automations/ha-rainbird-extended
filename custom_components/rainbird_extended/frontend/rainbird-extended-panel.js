const DOMAIN = "rainbird_extended";
const STOP_ACTION_STOP_IRRIGATION = "stop_irrigation";

class RainbirdExtendedPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._load();
    } else {
      this._updateControllerStatuses();
    }
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
    this._data = await this._hass.callWS({
      type: `${DOMAIN}/panel/config`,
    });
    this._render();
  }

  _renderLoading() {
    this.shadowRoot.innerHTML = `${this._styles()}<main><p>Loading Rain Bird Extended…</p></main>`;
  }

  _renderError(error) {
    this.shadowRoot.innerHTML = `${this._styles()}<main><section class="error">${this._escape(error?.message || "Unable to load the Rain Bird Extended dashboard.")}</section></main>`;
  }

  _render() {
    const controllers = this._data?.controllers || [];
    this.shadowRoot.innerHTML = `${this._styles()}
      <main>
        <header>
          <div>
            <h1>Rain Bird Extended</h1>
            <p>Controller settings, programs, and per-zone configuration.</p>
          </div>
          <button class="secondary" data-action="refresh">Refresh</button>
        </header>
        ${
          controllers.length
            ? `<div class="controller-list">${controllers.map((controller) => this._controllerCard(controller)).join("")}</div>`
            : `<section class="empty"><h2>No controllers configured</h2><p>Add a Rain Bird controller in Home Assistant, then set up Rain Bird Extended from Devices &amp; services.</p></section>`
        }
        ${this._flow ? this._flowDialog() : ""}
        ${this._pendingStop ? this._stopConfirmation() : ""}
      </main>`;
    this.shadowRoot.querySelector('[data-action="refresh"]')?.addEventListener("click", () => {
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
        ${controller.stop_button ? `<button class="danger" data-action="request-stop" data-stop-action="${this._escape(controller.stop_action || STOP_ACTION_STOP_IRRIGATION)}" data-button-entity-id="${this._escape(controller.stop_button)}">Stop irrigation</button>` : ""}
        <span class="muted">${this._escape(controller.title)} · ${this._escape(controller.state)}</span>
      </div>
      ${controller.stop_button ? `<p class="stop-note">Stops active watering only. Configured programs remain enabled.</p>` : ""}
      ${this._weatherAndRainDelay(controller)}
      ${
        controller.programs?.length
          ? `<div class="program-section"><h3>Programs</h3><div class="programs">${controller.programs.map((program) => this._programCard(program)).join("")}</div></div>`
          : ""
      }
      ${
        zones.length
          ? `<h3>Zone configuration</h3><div class="zones">${zones.map((zone) => this._zoneCard(zone)).join("")}</div>`
          : `<p class="muted">Zone configuration entities are not available yet.</p>`
      }
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
        : "Idle";
    return {
      availability: available ? "Online" : "Unavailable",
      availabilityClass: available ? "connected" : "disconnected",
      irrigation,
      irrigationClass: irrigation === "Irrigating"
        ? "irrigating"
        : irrigation === "Idle"
          ? "idle"
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

  _programCard(program) {
    const label = program.name
      ? `Program ${program.letter} · ${program.name}`
      : `Program ${program.letter}`;
    const status = program.configured
      ? `<span class="program-state configured">Configured</span><span class="program-next">Next run: ${this._formatProgramDate(program.next_run)}</span>`
      : `<span class="program-state">Not configured</span>`;
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

  _stopConfirmation() {
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
      if (pending.action !== STOP_ACTION_STOP_IRRIGATION || !pending.entityId) {
        throw new Error("The configured stop action is not supported.");
      }
      await this._hass.callService("button", "press", {
        entity_id: pending.entityId,
      });
      await this._refresh();
      this._showNotice("Active irrigation stopped.");
    } catch (error) {
      this._showNotice(error?.message || "Unable to stop irrigation.", true);
    }
  }

  _zoneCard(zone) {
    const name = zone.name || `Zone ${zone.zone}`;
    return `<article class="zone">
      <h4>${this._escape(name)}</h4>
      <div class="numbers">${zone.numbers.map((number) => this._numberControl(number, name)).join("")}</div>
    </article>`;
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
    if (flow.loading || !step) {
      return `<div class="backdrop"><section class="dialog"><p>Opening configuration…</p></section></div>`;
    }
    if (step.type === "menu") {
      const options = (step.menu_options || []).map((option) => {
        const label = this._localize(`component.${DOMAIN}.options.step.${step.step_id}.menu_options.${option}`, option.replaceAll("_", " "));
        return `<button class="menu-option" data-next-step="${this._escape(option)}">${this._escape(label)}<span>›</span></button>`;
      }).join("");
      return `<div class="backdrop"><section class="dialog">
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
        <footer><button class="secondary" data-action="close-flow">Cancel</button><button class="primary" data-action="submit-flow" ${flow.loading ? "disabled" : ""}>Save</button></footer>
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
      const translationKey = config.select?.translation_key;
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
      .status-group{display:flex;flex-wrap:wrap;gap:8px}.status{border-radius:20px;padding:6px 11px;font-size:12px;font-weight:600;white-space:nowrap}.connected,.idle{background:var(--success-color,#2e7d32);color:var(--text-primary-color,#fff)}.disconnected,.irrigating{background:var(--warning-color,#ed6c02);color:var(--text-primary-color,#fff)}.unknown{background:var(--secondary-background-color,#f1f3f4);color:var(--secondary-text-color)}
      .controller-actions{justify-content:flex-start;margin-top:17px;flex-wrap:wrap}.muted{font-size:13px;color:var(--secondary-text-color)}.stop-note{font-size:12px;margin-top:8px}
      .danger{background:var(--error-color,#db4437);color:var(--text-primary-color,#fff);font-weight:600}
      .weather-section{margin-top:22px}.weather-layout{display:grid;grid-template-columns:minmax(0,3fr) minmax(230px,1fr);gap:16px}.forecast-panel,.rain-delay-panel{border:1px solid var(--border);border-radius:10px;padding:16px}.weather-section h3{margin:0 0 12px}.forecast-days{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}.forecast-day{border:1px solid var(--border);border-radius:8px;padding:10px}.forecast-day h4{margin:0 0 6px}.forecast-day p{font-size:12px;margin-top:4px}.forecast-condition{text-transform:capitalize}.rain-delay-panel .number-row{margin:8px 0 12px}.rain-delay-panel .muted{line-height:1.5}
      .program-section{margin-top:22px}.program-section h3{margin:0 0 12px}.programs{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.program{border:1px solid var(--border);border-radius:10px;padding:13px 14px}.program h4{margin:0 0 9px}.program-details{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.program-state{display:inline-flex;border-radius:16px;padding:4px 9px;font-size:12px;font-weight:600;background:var(--secondary-background-color,#f1f3f4);color:var(--secondary-text-color)}.program-state.configured{background:var(--success-color,#2e7d32);color:var(--text-primary-color,#fff)}.program-next{font-size:13px;color:var(--secondary-text-color)}
      button{font:inherit;border:0;border-radius:8px;padding:10px 16px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.primary{background:var(--blue);color:var(--text-primary-color,#fff);font-weight:600}.secondary{background:var(--secondary-background-color,#f1f3f4);color:var(--primary-text-color)}.compact{padding:6px 10px;font-size:12px}
      .zones{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:12px}.zone{border:1px solid var(--border);border-radius:10px;padding:14px}.number-row{display:grid;gap:6px;margin:12px 0;font-size:13px}.number-input,.number-control{display:flex;align-items:center;gap:7px}.number-input input,.number-control input{min-width:0;flex:1}.number-input small,.number-control small,.number-control output{color:var(--secondary-text-color)}input,select{font:inherit;color:var(--primary-text-color);background:var(--input-fill-color,var(--card-background-color,#fff));border:1px solid var(--border);border-radius:7px;padding:9px;min-width:0;width:100%}input[type=range],input[type=checkbox]{width:auto}
      .empty{text-align:center;padding:48px 22px}.empty h2{margin-bottom:8px}
      .backdrop{position:fixed;inset:0;background:#0008;display:grid;place-items:center;padding:20px;z-index:10}.dialog{width:min(760px,100%);max-height:90vh;overflow:auto;background:var(--card-background-color,#fff);border-radius:16px;padding:24px;box-shadow:0 16px 48px #0004}.dialog-head{margin-bottom:12px}.icon-button{font-size:24px;padding:2px 8px;background:transparent;color:var(--primary-text-color)}.description{margin:0 0 18px;line-height:1.5;white-space:pre-wrap}
      .menu{display:grid;gap:8px}.menu-option{display:flex;justify-content:space-between;align-items:center;text-align:left;background:var(--secondary-background-color,#f1f3f4);color:var(--primary-text-color)}.menu-option span{font-size:22px;color:var(--secondary-text-color)}
      form{display:grid;gap:14px}.field{display:grid;gap:7px}.field[hidden]{display:none}.field-label{font-weight:500}.field small{color:var(--secondary-text-color);line-height:1.4}.field-error{color:var(--error-color,#db4437);font-size:13px}fieldset{border:1px solid var(--border);border-radius:10px;padding:14px;display:grid;gap:14px}legend{font-weight:600;padding:0 7px}.order-buttons{display:flex;gap:8px;margin-top:8px}
      footer{justify-content:flex-end;margin-top:22px;padding-top:16px;border-top:1px solid var(--border)}.notice{padding:12px 14px;margin:0 0 14px;border-radius:8px;background:var(--success-color,#2e7d32);color:#fff}.error{background:var(--error-color,#db4437);color:#fff}
      @media(max-width:700px){.weather-layout{grid-template-columns:1fr}}@media(max-width:600px){main{padding:18px 12px 36px}header{align-items:flex-start}h1{font-size:23px}.controller{padding:16px}.controller-head{flex-direction:column}.controller-actions{align-items:flex-start;flex-direction:column}.dialog{padding:18px}.zones{grid-template-columns:1fr}}
    </style>`;
  }
}

customElements.define("rainbird-extended-panel", RainbirdExtendedPanel);
