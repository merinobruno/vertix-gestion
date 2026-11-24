import {displayNumber, documentDigits, identificationGroup, writeIdentification} from "./identification.js";
import {addressFields} from "./address.js";

function identity(party) {
  return identificationGroup(party.id_type) + "/" + documentDigits(party.id_number);
}

export function fieldsFromReplacedParty(previous, current, editingName) {
  const clean = value => (value || "").trim().replace(/\s+/g, " ");
  const sameName = clean(current.name) === clean(previous.name);
  if (editingName ? sameName : identity(current) === identity(previous)) return null;
  const fields = [];
  if (editingName) fields.push("id_number");
  else if (sameName) fields.push("name");
  for (const key of Object.keys(addressFields)) {
    const oldValue = clean(previous.address?.[key]);
    if (oldValue && clean(current.address?.[key]) === oldValue) fields.push("address." + key);
  }
  return fields;
}

function node(tag, properties = {}) {
  return Object.assign(document.createElement(tag), properties);
}

export async function localDirectoryRequest(action, payload, signal) {
  const response = await fetch("/api/agenda/" + action, {
    method:"POST", headers:{"Content-Type":"application/json"}, cache:"no-store", body:JSON.stringify(payload), signal,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "No se pudo acceder a la agenda local.");
  return result;
}

export function attachDirectory(group, role, {read, apply, validate, directoryRequest}) {
  const noun = role === "client" ? "cliente" : "entidad";
  function lookupField(key, caption, max, wide) {
    const wrapper = node("div", {className:"native-field directory-name" + (wide ? " wide" : "")});
    const label = node("label", {className:"directory-label"});
    const input = node("input", {name:role + "." + key, type:"text", required:true, pattern:".{0," + max + "}", autocomplete:"off"});
    label.append(node("span", {textContent:caption}), input);
    wrapper.append(label);
    return [wrapper, input];
  }
  const [nameWrapper, name] = lookupField("name", "Nombre o razón social", 180, true);
  const [idWrapper, number] = lookupField("id_number", "Número de identificación", 40, false);
  number.inputMode = "numeric";
  group.prepend(nameWrapper);
  const type = group.querySelector('[name$=".id_type"]');
  type.after(idWrapper);
  const list = node("ul", {className:"directory-options", id:"suggestions-" + role, hidden:true});
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "Fichas guardadas");
  const count = node("span", {className:"visually-hidden"});
  count.setAttribute("role", "status");
  nameWrapper.append(list, count);

  const actions = node("div", {className:"directory-actions wide", hidden:true});
  const button = node("md-outlined-button", {type:"button", id:"save-" + role});
  const buttonLabel = document.createTextNode("Guardar " + noun);
  button.append(buttonLabel);
  const feedback = node("span");
  feedback.setAttribute("role", "status");
  actions.append(button, feedback);
  group.append(actions);

  let available = false;
  let timer = 0;
  let pending = null;
  let records = [];
  let active = -1;
  let selected = null;
  let anchor = name;
  let saving = false;
  let version = 0;

  function message(text, error = false) {
    feedback.textContent = text;
    feedback.classList.toggle("failed", error);
  }

  function close() {
    clearTimeout(timer);
    timer = 0;
    pending?.abort();
    pending = null;
    active = -1;
    records = [];
    list.hidden = true;
    list.replaceChildren();
    count.textContent = "";
    if (available) anchor.setAttribute("aria-expanded", "false");
    anchor.removeAttribute("aria-activedescendant");
  }

  function visible() {
    return available && !name.disabled && !group.closest("[hidden], [inert]");
  }

  function highlight(index) {
    active = index;
    for (const [i, option] of [...list.children].entries()) option.setAttribute("aria-selected", String(i === index));
    const option = list.children[index];
    anchor.setAttribute("aria-activedescendant", option.id);
    option.scrollIntoView({block:"nearest"});
  }

  function lookup(input, selectFirst = false) {
    close();
    anchor = input;
    const query = input.value.trim();
    if (!query || query.length > 240 || !visible()) return;
    input.closest(".directory-name").append(list, count);
    const search = new AbortController();
    pending = search;
    timer = setTimeout(async () => {
      timer = 0;
      try {
        const {parties} = await directoryRequest("search", {role, query}, search.signal);
        if (pending !== search || !visible() || input.value.trim() !== query || document.activeElement !== input) return;
        records = parties;
        for (const [index, party] of records.entries()) {
          const option = node("li", {id:list.id + "-" + party.id});
          option.setAttribute("role", "option");
          option.setAttribute("aria-selected", "false");
          option.dataset.index = index;
          option.append(node("strong", {textContent:party.name}), node("span", {textContent:party.id_type + " " + displayNumber(party.id_type, party.id_number)}));
          list.append(option);
        }
        list.hidden = !records.length;
        input.setAttribute("aria-expanded", String(Boolean(records.length)));
        count.textContent = records.length ? records.length + (records.length === 1 ? " coincidencia." : " coincidencias.") : "Sin coincidencias.";
        if (selectFirst && records.length) highlight(0);
      } catch (error) {
        if (pending === search && !search.signal.aborted && visible()) {
          message(error.message === "Failed to fetch" ? "No se pudo consultar la Agenda." : error.message, true);
        }
      } finally {
        if (pending === search) pending = null;
      }
    }, 130);
  }

  function choose(index) {
    const party = records[index];
    if (!party || !visible()) return;
    close();
    selected = party;
    apply(party);
    message("Ficha cargada.");
  }

  function detachOnChange(input) {
    if (!selected) return false;
    const fields = fieldsFromReplacedParty(selected, read(true), input === name);
    if (!fields) return false;
    selected = null;
    version++;
    close();
    for (const key of fields) {
      if (key === "id_number") writeIdentification(number, {id_type:type.value, id_number:""});
      else group.querySelector('[name="' + role + "." + key + '"]').value = "";
    }
    message("Completá los datos o elegí otra ficha.");
    return true;
  }

  for (const input of [name, number]) {
    input.addEventListener("input", () => { if (!detachOnChange(input)) message(""); lookup(input); });
    input.addEventListener("change", () => detachOnChange(input));
    input.addEventListener("blur", close);
    input.addEventListener("keydown", browseSuggestions);
  }
  type.addEventListener("change", () => detachOnChange(type));
  list.addEventListener("mousedown", event => event.preventDefault());
  list.addEventListener("click", event => {
    const option = event.target.closest('[role="option"]');
    if (option) choose(Number(option.dataset.index));
  });
  function browseSuggestions(event) {
    const input = event.currentTarget;
    if (!visible() || event.isComposing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!input.value.trim()) return;
      event.preventDefault();
      if (list.hidden) lookup(input, true);
      else if (event.key === "ArrowDown") highlight(Math.min(active + 1, records.length - 1));
      else highlight(active < 0 ? records.length - 1 : Math.max(0, active - 1));
    } else if (event.key === "Escape" || (event.key === "Enter" && (pending || !list.hidden))) {
      event.preventDefault();
      if (event.key === "Enter" && active >= 0) choose(active);
      else if (event.key === "Escape" || !pending) close();
    }
  }

  button.addEventListener("click", async () => {
    if (saving || !visible() || !validate()) return;
    const party = read();
    const snapshot = JSON.stringify(party);
    const revision = version;
    saving = true;
    button.disabled = true;
    message("Guardando…");
    try {
      const updating = selected && identity(selected) === identity(party);
      const result = updating ? await directoryRequest("update", {id:selected.id, revision:selected.revision, party})
        : await directoryRequest("save", {role, party, create_only:true});
      if (revision === version && visible() && JSON.stringify(read()) === snapshot) {
        selected = result.party;
        message(result.created ? "Ficha guardada." : "Ficha actualizada.");
      } else if (revision === version) message("");
    } catch (error) {
      if (revision === version) {
        if (visible() && JSON.stringify(read()) === snapshot) {
          message(error.message === "Failed to fetch" ? "No se pudo guardar la ficha." : error.message, true);
        } else message("");
      }
    } finally {
      saving = false;
      sync(available);
    }
  });

  function sync(enabled) {
    if (available !== enabled) {
      close();
      available = enabled;
      actions.hidden = !enabled;
      name.placeholder = enabled ? "Buscar por nombre, CUIT, CUIL o DNI" : "";
      for (const input of [name, number]) {
        if (enabled) {
          input.setAttribute("role", "combobox");
          input.setAttribute("aria-autocomplete", "list");
          input.setAttribute("aria-controls", list.id);
          input.setAttribute("aria-expanded", "false");
        } else {
          for (const attr of ["role", "aria-autocomplete", "aria-controls", "aria-expanded"]) input.removeAttribute(attr);
          message("");
        }
      }
    }
    if (!visible() && (pending || !list.hidden)) close();
    button.disabled = saving || !enabled || name.disabled;
    // Recheck silent browser autofill before validation or export.
    if (selected && identity(selected) !== identity(read())) detachOnChange(type);
    else if (selected && name.value !== selected.name) detachOnChange(name);
    const labelText = (selected ? "Actualizar " : "Guardar ") + noun;
    if (buttonLabel.data !== labelText) buttonLabel.data = labelText;
  }

  function reset() {
    version++;
    close();
    selected = null;
    message("");
  }
  return {sync, close, reset};
}
