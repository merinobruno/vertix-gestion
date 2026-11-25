import {bindIdentification, displayNumber, readIdentification, taxKind, writeIdentification} from "./identification.js";

export function setupAgenda({addAddress, addressFields, fieldProblem, fieldError, directoryRequest, readParty, writeParty,
  readLenderRepresentative, writeLenderRepresentative}) {
  const byId = id => document.getElementById(id);
  const tabs = byId("workspace-tabs");
  const opTab = byId("tab-operation");
  const agendaTab = byId("tab-agenda");
  const operation = byId("operation-view");
  const view = byId("agenda-view");
  const browse = byId("agenda-browse");
  const newButton = byId("agenda-new");
  const search = byId("agenda-search");
  const list = byId("agenda-list");
  const status = byId("agenda-status");
  const retry = byId("agenda-retry");
  const feedback = byId("agenda-feedback");
  const edit = byId("agenda-editor");
  const editorStatus = byId("agenda-editor-status");
  const field = key => edit.elements.namedItem(key);
  const components = Object.keys(addressFields);
  addAddress(byId("agenda-address"), "address", "Domicilio");
  bindIdentification(field("id_type"), field("id_number"), taxKind, fieldError);

  let available = false;
  let active = false;
  let editing = null;
  let saving = false;
  let pageOffset = 0;
  let pageLength = 0;
  let pageSize = 0;
  let request = null;
  let timer = 0;
  let focusRow = null;

  function stopListRequest() {
    clearTimeout(timer);
    request?.abort();
    request = null;
  }

  function editorMessage(message) {
    editorStatus.textContent = message;
    editorStatus.hidden = !message;
  }

  function setAddressRequirements() {
    const required = (editing && !editing.id) || components.some(key => field("address." + key).value.trim());
    for (const key of components) field("address." + key).required = required && key !== "floor" && key !== "apartment";
  }

  function toggleRepresentativeFields() {
    for (const group of edit.querySelectorAll("[data-agenda-rep]")) {
      group.hidden = !field(group.dataset.agendaRep).selected;
      for (const input of group.querySelectorAll("[name]")) input.disabled = group.hidden;
    }
  }

  function openEditor(party = {name:"", id_type:taxKind, id_number:""}) {
    stopListRequest();
    feedback.hidden = true;
    editing = party;
    field("name").value = party.name;
    writeIdentification(field("id_number"), party);
    for (const key of components) field("address." + key).value = party.address?.[key] || "";
    field("has_borrower_rep").selected = Boolean(party.borrower_representative);
    field("has_lender_rep").selected = Boolean(party.lender_representative);
    writeParty("representative", party.borrower_representative ?? {id_type:"DNI"}, edit);
    writeLenderRepresentative(edit, party.lender_representative);
    toggleRepresentativeFields();
    for (const input of edit.querySelectorAll("[data-reported]")) fieldError(input, "");
    setAddressRequirements();
    editorMessage("");
    browse.hidden = true;
    newButton.hidden = true;
    edit.hidden = false;
    const title = byId("agenda-editor-title");
    title.textContent = party.id ? "Editar ficha" : "Nueva ficha";
    byId("agenda-save").textContent = party.id ? "Guardar cambios" : "Guardar ficha";
    (party.id ? title : field("name")).focus();
  }

  function render(rows) {
    list.replaceChildren();
    for (const party of rows) {
      const li = document.createElement("li");
      const description = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = party.name;
      const number = document.createElement("small");
      number.textContent = party.id_type + " " + displayNumber(party.id_type, party.id_number);
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Editar";
      button.dataset.id = party.id;
      button.setAttribute("aria-label", "Editar " + party.name + ", " + number.textContent);
      button.addEventListener("click", () => openEditor(party));
      description.append(name, number);
      li.append(description, button);
      list.append(li);
    }
    if (focusRow !== null) {
      (list.querySelector('[data-id="' + focusRow + '"]') || search).focus();
      focusRow = null;
    }
  }

  function load(offset = 0, delay = 0) {
    stopListRequest();
    if (!active || !available || editing) return;
    const query = search.value.trim();
    const controller = new AbortController();
    request = controller;
    list.replaceChildren();
    byId("agenda-pages").hidden = true;
    retry.hidden = true;
    status.classList.remove("failed");
    status.textContent = "Buscando…";
    timer = setTimeout(async () => {
      try {
        const {parties, total, page_size} = await directoryRequest("list", {query, offset}, controller.signal);
        if (request !== controller) return;
        if (offset && !parties.length && total) { load(0); return; }
        pageOffset = offset;
        pageLength = parties.length;
        pageSize = page_size;
        const paginated = total > parties.length;
        status.textContent = total ? (paginated ? (offset + 1) + "–" + (offset + parties.length) + " de " : "")
          + total + (total === 1 ? " ficha" : " fichas") : (query ? "Sin coincidencias." : "Todavía no hay fichas guardadas.");
        byId("agenda-pages").hidden = !paginated;
        byId("agenda-prev").disabled = offset === 0;
        byId("agenda-next").disabled = offset + parties.length >= total;
        render(parties);
      } catch (error) {
        if (request !== controller) return;
        status.textContent = error.message === "Failed to fetch" ? "No se pudo consultar la Agenda." : error.message;
        status.classList.add("failed");
        retry.onclick = () => load(offset);
        retry.hidden = false;
      } finally {
        if (request === controller) request = null;
      }
    }, delay);
  }

  function agendaInUrl() {
    const wasAgenda = location.hash.startsWith("#agenda/");
    if (wasAgenda || location.hash.startsWith("#operacion/")) {
      history.replaceState(history.state, "", location.pathname + location.search + (wasAgenda ? "#agenda" : ""));
    }
    return location.hash === "#agenda";
  }

  function useView(showAgenda, changeUrl = false) {
    if (!available && showAgenda) return;
    if (changeUrl) {
      const fragment = showAgenda ? "#agenda" : "";
      if (location.hash !== fragment) {
        const url = new URL(location.href);
        url.hash = fragment;
        history.pushState(null, "", url);
      }
    }
    const wasActive = active;
    active = showAgenda;
    operation.hidden = active;
    view.hidden = !active;
    opTab.setAttribute("aria-selected", String(!active));
    agendaTab.setAttribute("aria-selected", String(active));
    opTab.tabIndex = active ? -1 : 0;
    agendaTab.tabIndex = active ? 0 : -1;
    document.title = "Panel documental · " + (active ? byId("agenda-title").textContent : "Nueva operación");
    if (!active) stopListRequest();
    else if (!wasActive && !editing) load(pageOffset);
  }

  function returnToList(savedParty) {
    const creating = !editing.id;
    focusRow = savedParty?.id ?? editing.id ?? null;
    editing = null;
    edit.hidden = true;
    newButton.hidden = false;
    browse.hidden = false;
    feedback.textContent = savedParty ? (creating ? "Ficha guardada." : "Ficha actualizada.") : "";
    feedback.hidden = !savedParty;
    if (creating && savedParty) {
      search.value = "";
      pageOffset = 0;
    }
    load(pageOffset);
    if (creating && !savedParty) newButton.focus();
  }

  opTab.addEventListener("click", () => useView(false, true));
  agendaTab.addEventListener("click", () => useView(true, true));
  tabs.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const showAgenda = event.key === "Home" ? false : event.key === "End" ? true : !active;
    (showAgenda ? agendaTab : opTab).focus();
    useView(showAgenda, true);
  });
  window.addEventListener("hashchange", () => useView(agendaInUrl()));
  newButton.addEventListener("click", () => openEditor());
  search.addEventListener("input", () => { feedback.hidden = true; focusRow = null; load(0, 120); });
  byId("agenda-prev").addEventListener("click", () => load(Math.max(0, pageOffset - pageSize)));
  byId("agenda-next").addEventListener("click", () => load(pageOffset + pageLength));
  byId("agenda-cancel").addEventListener("click", () => returnToList());
  edit.addEventListener("change", toggleRepresentativeFields);
  edit.addEventListener("input", () => {
    setAddressRequirements();
    for (const input of edit.querySelectorAll("[data-reported]")) fieldError(input, input.disabled ? "" : fieldProblem(input));
  });
  edit.addEventListener("submit", async event => {
    event.preventDefault();
    if (!editing || saving || !active || !available) return;
    const selected = editing;
    setAddressRequirements();
    for (const input of edit.querySelectorAll("md-outlined-text-field, md-outlined-select")) {
      if (input.disabled) continue;
      const problem = fieldProblem(input);
      if (problem) { fieldError(input, problem); input.focus(); return; }
    }
    const party = {name:field("name").value, ...readIdentification(field("id_number"))};
    party.address = Object.fromEntries(components.map(key => [key, field("address." + key).value]));
    if (field("has_borrower_rep").selected) party.borrower_representative = readParty("representative", edit);
    else if (selected.borrower_representative) party.borrower_representative = null;
    if (field("has_lender_rep").selected) party.lender_representative = readLenderRepresentative(edit);
    else if (selected.lender_representative) party.lender_representative = null;
    saving = true;
    edit.inert = true;
    editorMessage("");
    try {
      const result = selected.id
        ? await directoryRequest("update", {id:selected.id, revision:selected.revision, party})
        : await directoryRequest("save", {create_only:true, party});
      if (editing === selected) returnToList(result.party);
    } catch (error) {
      if (editing === selected) editorMessage(error.message === "Failed to fetch" ? "No se pudo guardar la ficha." : error.message);
    } finally {
      saving = false;
      edit.inert = false;
    }
  });

  function setAvailable(enabled) {
    if (available === enabled) {
      if (active) document.title = "Panel documental · " + byId("agenda-title").textContent;
      return;
    }
    available = enabled;
    tabs.hidden = !enabled;
    if (!enabled) {
      stopListRequest();
      editing = null;
      newButton.hidden = false;
      edit.reset();
      edit.hidden = feedback.hidden = retry.hidden = true;
      for (const field of edit.querySelectorAll("md-outlined-text-field")) field.value = "";
      search.value = "";
      focusRow = null;
      pageOffset = 0;
      list.replaceChildren();
      status.textContent = "";
      editorMessage("");
    }
    browse.hidden = !enabled || Boolean(editing);
    if (enabled) {
      operation.setAttribute("role", "tabpanel");
      operation.setAttribute("aria-labelledby", opTab.id);
    } else {
      operation.removeAttribute("role");
      operation.removeAttribute("aria-labelledby");
    }
    useView(enabled && agendaInUrl());
  }
  try { localStorage.removeItem("vertix.agenda.team-link"); } catch { /* Old bookmarks also work with storage disabled. */ }
  return {setAvailable, reload:() => load(0)};
}
