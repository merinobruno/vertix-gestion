import {icon} from "./material.js";

const form = document.querySelector("#operation");
const byId = (id) => document.getElementById(id);
const control = (name) => form.elements.namedItem(name);
const datePickers = new WeakMap();
const fieldValue = (field) => (datePickers.get(field) || field).value;
const valueOf = (name) => name === "format"
  ? [...form.querySelectorAll('md-radio[name="format"]')].find(radio => radio.checked)?.value || "docx"
  : fieldValue(control(name));
const checked = (name) => control(name).selected;
const pages = ["datos", "partes", "valores"];
const editable = "input, md-outlined-text-field, md-outlined-select";
const formComponents = editable + ", md-switch, md-checkbox, md-radio, md-text-button";
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
const ease = "cubic-bezier(0.2, 0, 0, 1)";
let current = -1;
let movement = [];
let changed = false;
let busy = false;
let templateService = null;
let combinedService = null;
let migrationWindow = null;
let importRevision = 0;
let importing = false;
let importedPrincipal = null;
const bankFields = ["bank_principal","bank_name","account_type","account_number","cbu","account_holder","account_id",
  "lender_rep_name","lender_rep_id","lender_rep_title","borrower_title","guarantor_title","payment_method"];
const localPanel = location.protocol === "http:" && ["127.0.0.1", "localhost"].includes(location.hostname);
const companion = localPanel && ["8765", "8766"].includes(location.port) ? new URL("/", location.href) : null;
if (companion) companion.port = location.port === "8766" ? "8765" : "8766";
byId("open-service").hidden = !companion;

function element(tag, props = {}) {
  return Object.assign(document.createElement(tag), props);
}

function writeField(field, value) {
  const picker = datePickers.get(field);
  if (picker) {
    picker.value = value;
    field.value = picker.value.split("-").reverse().join("/");
    field.setCustomValidity("");
  } else {
    field.value = value;
  }
}

function enableNumericDate(field) {
  const original = field.value;
  const label = field.closest("label");
  const chooseDate = "Abrir calendario de " + label.querySelector("span").textContent.toLowerCase();
  Object.assign(field, {type:"text", inputMode:"numeric", placeholder:"DD/MM/AAAA", autocomplete:"off"});
  const picker = element("input", {type:"date", max:"9999-12-31", ariaLabel:chooseDate});
  datePickers.set(field, picker);
  const calendar = element("span", {className:"date-calendar"});
  const trigger = element("button", {type:"button", ariaLabel:chooseDate});
  trigger.append(icon("calendar"));
  calendar.append(trigger, picker);
  label.after(calendar);
  if (typeof picker.showPicker === "function") {
    picker.tabIndex = -1;
    picker.setAttribute("aria-hidden", "true");
    trigger.addEventListener("click", () => picker.showPicker());
  } else {
    trigger.tabIndex = -1;
    trigger.setAttribute("aria-hidden", "true");
  }
  writeField(field, original);
  field.addEventListener("input", event => {
    let value = field.value;
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      value = value.split("-").reverse().join("/");
    } else if (field.selectionStart === value.length && !event.inputType?.startsWith("delete")) {
      // Insert separators as the user appends digits. Leave edits inside the date at their original caret.
      value = value.replace(/^(\d{2})(\d{1,6})$/, "$1/$2").replace(/^(\d{2}\/\d{2})(\d{1,4})$/, "$1/$2");
    }
    if (value !== field.value) field.value = value;
    const parts = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(value);
    picker.value = parts ? [parts[3], parts[2].padStart(2,"0"), parts[1].padStart(2,"0")].join("-") : "";
    field.setCustomValidity(value && !picker.value ? "Ingresá una fecha válida en formato DD/MM/AAAA." : "");
  });
  field.addEventListener("blur", () => { if (picker.value) writeField(field, picker.value); });
  for (const event of ["input", "change"]) {
    picker.addEventListener(event, () => writeField(field, picker.value));
  }
}

function appendField(container, caption, node, wide = false) {
  if (node.localName === "input" && node.type === "date") {
    const label = element("label", {className:"date-field"});
    label.append(element("span",{textContent:caption}),node);
    const wrapper = element("div",{className:"native-field"});
    wrapper.append(label);
    container.append(wrapper);
    enableNumericDate(node);
  } else {
    node.label = caption;
    if (wide) node.classList.add("wide");
    container.append(node);
  }
  return node;
}

function createParty(group) {
  const key = group.dataset.party;
  const natural = key === "representative" || key === "guarantor";
  appendField(group, natural ? "Nombre y apellido" : "Nombre o razón social",
    element("md-outlined-text-field", {name: key + ".name", pattern: ".{0,180}", required: true}), true);
  const types = element("md-outlined-select", {name: key + ".id_type"});
  for (const id of ["CUIT", "DNI", "CUIL"]) {
    const option = element("md-select-option",{value:id});
    if (id === (natural ? "DNI" : "CUIT")) option.setAttribute("selected", "");
    option.append(element("span",{slot:"headline",textContent:id}));
    types.append(option);
  }
  appendField(group, "Tipo de identificación", types);
  appendField(group, "Número de identificación",
    element("md-outlined-text-field", {name: key + ".id_number", pattern: ".{0,40}", required: true}));
  appendField(group, "Domicilio", element("md-outlined-text-field", {name: key + ".address", pattern: ".{0,240}"}), true);
}

for (const group of form.querySelectorAll("[data-party]")) createParty(group);
for (const group of form.querySelectorAll("[data-treatment]")) {
  const name = group.dataset.treatment;
  const select = element("md-outlined-select",{name});
  for (const [text,value] of [["Sr.","el Sr."],["Sra.","la Sra."]]) {
    const option = element("md-select-option",{value});
    if ((name === "guarantor_title") === (text === "Sra.")) option.setAttribute("selected","");
    option.append(element("span",{slot:"headline",textContent:text}));
    select.append(option);
  }
  appendField(group,"Tratamiento",select);
}

function settlePanels() {
  const running = movement;
  movement = [];
  running.forEach(animation => animation.cancel());
  byId("panel-stage").classList.remove("moving");
  for (const [i,id] of pages.entries()) {
    const panel = byId(id);
    panel.classList.remove("leaving");
    panel.hidden = i !== current;
    panel.inert = i !== current;
  }
}

function showPage(index, focus = false, animate = true) {
  if (index < 0 || index >= pages.length) return;
  if (index === current) {
    if (focus) byId("tab-"+pages[index]).focus();
    return;
  }
  const previous = current;
  settlePanels();
  const before = previous >= 0 ? byId(pages[previous]) : null;
  current = index;
  const next = byId(pages[index]);
  const tabs = byId("form-tabs");
  if (tabs.activeTabIndex !== index) tabs.activeTabIndex = index;
  if (before) before.inert = true;
  next.hidden = false;
  next.inert = false;
  if (before && animate && !reduced.matches) {
    const stage = byId("panel-stage");
    const oldHeight = before.offsetHeight;
    const newHeight = next.offsetHeight;
    const direction = index > previous ? 1 : -1;
    const timing = {duration:280,easing:ease,fill:"both"};
    stage.classList.add("moving");
    before.classList.add("leaving");
    const outgoing = before.animate([{transform:"translateX(0)"},{transform:"translateX("+(-100*direction)+"%)"}],timing);
    const incoming = next.animate([{transform:"translateX("+(100*direction)+"%)"},{transform:"translateX(0)"}],timing);
    const sizing = stage.animate([{height:oldHeight+"px"},{height:newHeight+"px"}],timing);
    const batch = [outgoing,incoming,sizing];
    movement = batch;
    Promise.all(batch.map(a => a.finished)).then(() => {
      if (movement === batch) settlePanels();
    }).catch(() => {});
  } else {
    settlePanels();
  }
  byId("previous").hidden = current === 0;
  byId("next").hidden = current === pages.length - 1;
  if (focus) byId("tab-"+pages[index]).focus();
}

byId("form-tabs").addEventListener("change", event => {
  event.stopPropagation();
  showPage(event.currentTarget.activeTabIndex);
});
reduced.addEventListener("change", () => { if (reduced.matches && movement.length) settlePanels(); });
byId("previous").addEventListener("click", () => showPage(Math.max(0, current - 1), true));
byId("next").addEventListener("click", () => showPage(Math.min(2, current + 1), true));

function createRow(kind, values = {}, focus = false) {
  const holder = byId(kind);
  if (holder.querySelectorAll(".row-card:not([data-removing])").length >= 60) {
    statusMessage("Se pueden cargar hasta 60 filas.", true);
    return;
  }
  const cheque = kind === "cheques";
  const card = element("div", {className: "row-card"});
  const top = element("div", {className: "row-top"});
  const remove = element("md-text-button", {type: "button", className: "remove", textContent: "Quitar"});
  remove.prepend(icon("delete"));
  remove.addEventListener("click", () => {
    if (card.dataset.removing) return;
    card.dataset.removing = "true";
    card.inert = true;
    changed = true;
    refresh();
    const neighbor = card.nextElementSibling || card.previousElementSibling;
    (neighbor?.querySelector(".remove") || byId(cheque ? "add-cheque" : "add-installment")).focus();
    if (reduced.matches) card.remove();
    else {
      const animation = card.animate([{opacity:1,transform:"translateY(0)"},{opacity:0,transform:"translateY(-8px)"}],{duration:150,easing:"cubic-bezier(.4,0,1,1)"});
      animation.finished.then(() => card.remove(), () => card.remove());
    }
  });
  top.append(element("b"), remove);
  const fields = element("div", {className: cheque ? "row-fields" : "row-fields installment"});
  const spec = cheque ? [["number","Nº de cheque","text"], ["bank","Banco","text"]] : [];
  spec.push(["due","Vencimiento","date"],["amount",cheque ? "Importe nominal" : "Importe de la cuota","number"],
    ["note_percent","Adicional del pagaré (%)","number"]);
  for (const [key,title,type] of spec) {
    const input = element(type === "date" ? "input" : "md-outlined-text-field", {type, value: values[key] ?? (key === "note_percent" ? "0" : "")});
    if (type === "text") input.pattern = ".{0,120}";
    input.dataset.field = key;
    if (type === "number") {
      input.min = key === "note_percent" ? "0" : "0.01";
      input.max = "999999999999.99";
      input.step = "0.01";
      input.inputMode = "decimal";
      input.placeholder = "0,00";
    }
    appendField(fields, title, input);
  }
  const preview = element("p",{className:"note-preview",textContent:"Importe del pagaré"});
  preview.append(element("strong",{textContent:"—"}));
  fields.append(preview);
  if (values.reference_note || values.note_formula_missing) {
    if (values.reference_note) card.dataset.referenceNote = values.reference_note;
    if (values.note_formula_missing) card.dataset.noteFormulaMissing = "true";
    const reference = element("p",{className:"note-reference"});
    reference.textContent = values.reference_note ?
      "El Excel indica " + moneyFormat("ARS").format(Number(values.reference_note)) + ". Indicá el porcentaje que corresponda." :
      "Excel no guardó el resultado de PAGARÉ. Indicá el porcentaje o volvé a importar la planilla recalculada.";
    fields.append(reference);
  }
  if (!cheque) {
    const details = element("div",{className:"row-breakdown"});
    for (const [key,caption] of [["capital","Capital"],["interest","Interés"],["vat","IVA"]]) {
      const input = element("md-outlined-text-field",{type:"number",value:values[key] ?? "",min:"0",
        max:"999999999999.99",step:"0.01",inputMode:"decimal"});
      input.dataset.field = key;
      appendField(details,caption,input);
    }
    fields.append(details);
  }
  card.append(top,fields);
  holder.append(card);
  if (focus) {
    card.querySelector("[data-field]").focus();
    if (!reduced.matches) card.animate([{opacity:0,transform:"translateY(8px)"},{opacity:1,transform:"translateY(0)"}],{duration:210,easing:ease});
  }
}

function selectedDocs() {
  return [...form.querySelectorAll('md-checkbox[name="documents"]')].filter(box => box.checked).map(box => box.value);
}

function supported(docs) {
  const demand = valueOf("note_mode") === "demand";
  return docs.filter(doc => {
    if (doc === "cesion") return true;
    if (doc === "pagares" && demand && checked("guarantor_enabled")) return false;
    const currency = doc === "pagares" && demand ? "USD" : "ARS";
    return valueOf("currency") === currency;
  });
}

function flagsFor(docs) {
  const loan = docs.includes("mutuo");
  const demand = docs.includes("pagares") && valueOf("note_mode") === "demand";
  const notes = docs.includes("pagares") && !demand;
  const transfer = docs.includes("cesion");
  const credit = loan || notes;
  const bank = valueOf("payment_method") === "bank";
  const linked = !bank && checked("linked");
  return {loan, notes, demand, transfer, credit, requestedNotes: selectedDocs().includes("pagares"),
    requestedCredit: selectedDocs().some(doc => doc !== "cesion"),
    bank, linked, bankLoan:bank && loan, chequeLoan:!bank && loan, linkOption:!bank && credit,
    guaranteeLoan:loan && checked("guarantor_enabled"), rep: loan && !bank && checked("represented"),
    entity: loan || (notes && !checked("different_beneficiary")) || (transfer && !checked("different_assignee")),
    beneficiary: notes && checked("different_beneficiary"),
    assignee: transfer && checked("different_assignee"),
    payment: notes && checked("separate_payment"),
    cheques: transfer || (credit && linked),
    needCheques: transfer || (credit && linked),
    independent: credit && !linked};
}

function readRows(kind) {
  return [...byId(kind).querySelectorAll(".row-card:not([data-removing])")].map(row => {
    const info = {};
    for (const input of row.querySelectorAll("[data-field]")) info[input.dataset.field] = fieldValue(input).trim();
    if (row.dataset.referenceNote) info.reference_note = row.dataset.referenceNote;
    if (row.dataset.noteFormulaMissing) info.note_formula_missing = true;
    return info;
  }).filter(row => Object.entries(row).some(([key,value]) => key !== "note_percent" && value !== ""));
}

function readParty(key) {
  return Object.fromEntries(["name","id_type","id_number","address"].map(keyName => [keyName,valueOf(key+"."+keyName)]));
}

function payload(docs = selectedDocs()) {
  const bank = valueOf("payment_method") === "bank";
  const result = {documents: docs, installments_mode: !bank && checked("linked") ? "cheques" : "manual",
    cheques: readRows("cheques"), installments: readRows("installments")};
  for (const key of ["place","issue_date","currency","net","penalty","payment_address","format","note_mode",
    "demand_payee","demand_id","demand_amount","demand_consideration",...bankFields]) result[key] = valueOf(key);
  for (const key of ["represented","guarantor_enabled","different_beneficiary","different_assignee","separate_payment","combined_document"]) result[key] = checked(key);
  for (const key of ["client","entity","representative","beneficiary","assignee","guarantor"]) result[key] = readParty(key);
  result.cheque_net = result.net;
  if (bank) {
    result.net = result.bank_principal;
    result.represented = false;
  }
  return result;
}

function moneyFormat(currency) {
  return new Intl.NumberFormat("es-AR",{style:"currency",currency,currencyDisplay:"code",minimumFractionDigits:2});
}

function minorUnits(text) {
  const match = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(text);
  return match ? BigInt(match[1])*100n + BigInt((match[2] || "").padEnd(2,"0")) : null;
}

function notePreview(amount, percent, fmt) {
  const cents = minorUnits(amount);
  const basisPoints = minorUnits(percent);
  if (!cents || basisPoints === null) return "—";
  const result = (cents * (10000n + basisPoints) + 5000n) / 10000n;
  return result > 99999999999999n ? "Fuera de rango" : fmt.format(Number(result)/100);
}

function statusMessage(text, error = false) {
  const node = byId("status");
  node.hidden = !text;
  node.textContent = text;
  node.classList.toggle("error",error);
}

let nextErrorId = 0;

function validationMessage(field) {
  const validity = field.validity;
  if (datePickers.has(field)) return validity.valueMissing ? "Completá la fecha." : field.validationMessage;
  if (validity.badInput) return "Ingresá un número válido.";
  if (validity.valueMissing) return field.localName === "md-outlined-select" ? "Elegí una opción." : "Completá este campo.";
  const numbers = new Intl.NumberFormat("es-AR",{maximumFractionDigits:2});
  if (validity.rangeUnderflow) return "El mínimo es " + numbers.format(Number(field.min)) + ".";
  if (validity.rangeOverflow) return "El máximo es " + numbers.format(Number(field.max)) + ".";
  if (validity.stepMismatch) return "Usá hasta dos decimales.";
  const length = /^\.\{0,(\d+)\}$/.exec(field.pattern || "");
  if (validity.patternMismatch && field.name === "cbu") return "Ingresá los 22 dígitos del CBU.";
  if (validity.patternMismatch && length) return "Usá hasta " + length[1] + " caracteres.";
  return "Revisá este valor.";
}

function fieldError(field, message) {
  field.toggleAttribute("data-reported", Boolean(message));
  if (field.localName !== "input") {
    field.errorText = message;
    field.error = Boolean(message);
    return;
  }
  const wrapper = field.closest(".native-field");
  let hint = wrapper.querySelector(".field-error");
  if (message) {
    if (!hint) {
      hint = element("p",{className:"field-error", id:"field-error-" + (++nextErrorId)});
      hint.setAttribute("role","alert");
      wrapper.append(hint);
    }
    hint.textContent = message;
    field.setAttribute("aria-invalid","true");
    field.setAttribute("aria-describedby",hint.id);
  } else {
    hint?.remove();
    field.removeAttribute("aria-invalid");
    field.removeAttribute("aria-describedby");
  }
}

function refresh() {
  const docs = selectedDocs();
  const available = supported(docs);
  const f = flagsFor(available);
  const currency = valueOf("currency") || "ARS";
  const fmt = moneyFormat(currency);
  byId("values-tab-label").textContent = f.demand ? (f.transfer ? "Cheques e importe" : "Importe") : f.bank && !f.transfer ? "Cuotas" : "Cheques y cuotas";
  for (const box of form.querySelectorAll('md-checkbox[name="documents"]')) {
    box.closest("label").classList.toggle("is-selected",box.checked);
  }
  for (const radio of form.querySelectorAll('md-radio[name="format"]')) {
    radio.closest("label").classList.toggle("is-selected",radio.checked);
  }
  for (const region of form.querySelectorAll("[data-condition]")) region.hidden = !f[region.dataset.condition];
  for (const node of form.querySelectorAll(formComponents)) {
    if (node.closest("[data-condition]")) node.disabled = Boolean(node.closest("[data-condition][hidden], [data-removing]"));
  }
  control("client.address").required = f.loan && !f.rep;
  control("client.id_number").required = f.loan && !f.rep;
  control("entity.id_number").required = f.bankLoan || (f.notes && !f.beneficiary) || (f.transfer && !f.assignee);
  control("entity.address").required = f.loan || (f.notes && !f.beneficiary && !f.payment);
  control("representative.address").required = f.loan;
  control("beneficiary.address").required = f.notes && !f.payment;
  control("guarantor.address").required = f.guaranteeLoan;
  for (const kind of ["cheques","installments"]) {
    for (const [index,row] of [...byId(kind).querySelectorAll(".row-card:not([data-removing])")].entries()) {
      const isCheque = kind === "cheques";
      const title = (isCheque ? "Cheque " : "Cuota ") + (index + 1);
      row.querySelector("b").textContent = title;
      row.querySelector(".remove").setAttribute("aria-label","Quitar "+title.toLowerCase());
      const inputs = Array.from(row.querySelectorAll("[data-field]"));
      const active = isCheque ? f.needCheques : f.independent;
      const needsPercent = active && f.notes && (!isCheque || f.linked);
      const details = row.querySelector(".row-breakdown");
      const hasDetails = details && f.loan && (f.bankLoan || [...details.querySelectorAll("[data-field]")].some(i => i.value !== ""));
      if (details) details.hidden = !hasDetails;
      const percentField = row.querySelector('[data-field="note_percent"]');
      percentField.hidden = !needsPercent;
      const preview = row.querySelector(".note-preview");
      preview.hidden = !needsPercent;
      preview.querySelector("strong").textContent = notePreview(row.querySelector('[data-field="amount"]').value, percentField.value, fmt);
      const reference = row.querySelector(".note-reference");
      if (reference) reference.hidden = !needsPercent || percentField.value !== "";
      for (const input of inputs) {
        input.disabled = !active || input.hidden || Boolean(input.closest(".row-breakdown[hidden]"));
        input.required = !input.disabled && (input.dataset.field !== "bank" || f.transfer);
      }
    }
  }
  for (const field of form.querySelectorAll("[data-reported]")) {
    fieldError(field, field.disabled || field.validity.valid ? "" : validationMessage(field));
  }
  byId("no-values").hidden = docs.length > 0;
  const notice = byId("draft-notice");
  notice.hidden = docs.length === available.length;
  if (!notice.hidden) {
    const missing = docs.filter(doc => !available.includes(doc));
    const parts = [];
    if (missing.includes("mutuo") || (missing.includes("pagares") && valueOf("note_mode") !== "demand")) {
      parts.push("Los modelos de mutuo y pagarés por cuota requieren pesos.");
    }
    if (missing.includes("pagares") && valueOf("note_mode") === "demand") {
      parts.push("El pagaré a la vista requiere dólares sin garante.");
    }
    if (available.length === 1 && f.transfer) parts.push("Se descargará la cesión.");
    notice.textContent = parts.join(" ");
  }
  const cheques = f.cheques ? readRows("cheques") : [];
  const portions = f.linked ? cheques : readRows("installments");
  byId("note-original").hidden = f.bank || !f.notes || portions.length < 2;
  function totalText(rows) {
    const valid = rows.filter(r => r.amount !== "" && Number(r.amount) > 0);
    const cents = valid.reduce((sum,r) => sum+Math.round(Number(r.amount)*100),0);
    return valid.length ? fmt.format(cents/100) : "—";
  }
  byId("sum-currency").textContent = currency;
  const principal = valueOf(f.bank ? "bank_principal" : "net");
  byId("sum-net-label").textContent = f.bank ? "Capital del préstamo" : "Neto entregado";
  byId("sum-net").textContent = principal !== "" ? fmt.format(Number(principal)) : "—";
  byId("sum-cheques").textContent = totalText(cheques);
  byId("sum-installments").textContent = totalText(portions);
  byId("sum-demand").textContent = valueOf("demand_amount") ? fmt.format(Number(valueOf("demand_amount"))) : "—";
  const shown = {net:f.loan, cheques:f.cheques, notes:f.credit, demand:f.demand};
  for (const row of form.querySelectorAll("[data-summary]")) row.hidden = !shown[row.dataset.summary];
  const files = [];
  if (f.loan) files.push("Mutuo con anexo");
  if (f.notes) files.push(portions.length ? (portions.length === 1 ? "1 pagaré" : portions.length+" pagarés") : "Pagarés por cuota");
  if (f.demand) files.push("Pagaré a la vista");
  if (f.transfer) files.push("Cesión de cheques");
  byId("file-list").replaceChildren(...files.map(x => element("li",{textContent:x})));
  byId("file-list").hidden = files.length === 0;
  const format = valueOf("format");
  const documentCount = Number(f.loan) + Number(f.transfer) + Number(f.demand) + (f.notes ? Math.max(1,portions.length) : 0);
  const fileCount = (checked("combined_document") ? Math.min(1,documentCount) : documentCount) * (format === "both" ? 2 : 1);
  let label = "Descargar";
  if (fileCount > 1) label = "Descargar ZIP";
  if (fileCount === 1) label = format === "pdf" ? "Descargar PDF" : "Descargar Word";
  byId("download-label").textContent = label;
  const serviceMissing = templateService === false || (checked("combined_document") && combinedService === false);
  byId("service-notice").hidden = !serviceMissing;
  byId("generate").disabled = busy || importing || serviceMissing || (docs.length > 0 && available.length === 0);
}

function localDate(offset = 0) {
  const day = new Date();
  day.setDate(day.getDate() + offset);
  return [day.getFullYear(),String(day.getMonth()+1).padStart(2,"0"),String(day.getDate()).padStart(2,"0")].join("-");
}

function sample() {
  if (changed && !window.confirm("¿Reemplazar los datos cargados por el ejemplo ficticio?")) return;
  const combined = checked("combined_document");
  importRevision++;
  importing = false;
  importedPrincipal = null;
  annexStatus("");
  form.reset();
  control("combined_document").selected = combined;
  for (const field of form.querySelectorAll("[data-reported]")) fieldError(field,"");
  const sampleFields = {place:"Ciudad Ejemplo", issue_date:localDate(), currency:"ARS", net:"90000", penalty:"3",
    demand_payee:"Ada Ejemplo", demand_id:"00.000.001", demand_amount:"2500", demand_consideration:"una operación de ejemplo",
    "client.name":"Comercial de Ejemplo S.A.", "client.id_type":"CUIT", "client.id_number":"00-00000000-0", "client.address":"Calle de Prueba 120, Ciudad Ejemplo",
    "entity.name":"Financiera Ficticia S.A.", "entity.id_type":"CUIT", "entity.id_number":"00-00000001-0", "entity.address":"Avenida de Prueba 40, Ciudad Ejemplo"};
  for (const [name,value] of Object.entries(sampleFields)) writeField(control(name), value);
  byId("cheques").replaceChildren();
  byId("installments").replaceChildren();
  createRow("cheques",{number:"PRUEBA-001", bank:"Banco Ficticio", due:localDate(30), amount:"52000"});
  createRow("cheques",{number:"PRUEBA-002", bank:"Banco Ficticio", due:localDate(60), amount:"54000"});
  createRow("installments");
  changed = false;
  showPage(0,false,false);
  refresh();
  statusMessage("Ejemplo ficticio cargado.");
}
byId("example").addEventListener("click",sample);
control("payment_method").addEventListener("change",() => {
  if (valueOf("payment_method") === "bank") {
    form.querySelector('md-checkbox[value="cesion"]').checked = false;
  }
  refresh();
});

function annexStatus(message, error = false) {
  const status = byId("annex-status");
  status.textContent = message;
  status.hidden = !message;
  status.classList.toggle("draft",error);
}

async function importFile(file) {
  const revision = ++importRevision;
  importing = false;
  if (!file || !file.name.toLowerCase().endsWith(".xlsx") || !file.size || file.size > 4*1024*1024) {
    annexStatus("Elegí un archivo .xlsx de hasta 4 MB.",true);
    refresh();
    return;
  }
  importing = true;
  refresh();
  annexStatus("Leyendo el Anexo I…");
  try {
    const response = await fetch("/api/anexo",{method:"POST",headers:{"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},body:file});
    const result = await response.json();
    if (revision !== importRevision) return;
    if (!response.ok) throw new Error(result.error || "No se pudo importar el Excel.");
    const previous = readRows("installments").length;
    const capital = valueOf("bank_principal");
    if ((previous || (capital && result.principal && Number(capital) !== Number(result.principal))) &&
        !window.confirm("¿Reemplazar las cuotas y el capital del préstamo por los de este Excel?")) {
      annexStatus("Se conservó la carga anterior.");
      return;
    }
    control("linked").selected = false;
    byId("installments").replaceChildren();
    for (const item of result.installments) createRow("installments",item);
    if (result.principal != null || capital === importedPrincipal) control("bank_principal").value = result.principal ?? "";
    importedPrincipal = result.principal;
    changed = true;
    const unresolved = result.installments.some(item => item.note_percent === "");
    annexStatus(result.installments.length + " cuotas importadas de " + file.name + "." +
      (unresolved ? " Indicá el porcentaje de los pagarés señalados." : "") +
      (valueOf("payment_method") === "bank" && !valueOf("bank_principal") ? " Completá el capital del préstamo en Operación." : ""),unresolved);
  } catch (error) {
    if (revision === importRevision) annexStatus(error.message === "Failed to fetch" ? "No se pudo conectar con el panel." : error.message,true);
  } finally {
    if (revision === importRevision) {
      importing = false;
      refresh();
    }
  }
}
byId("choose-annex").addEventListener("click",() => byId("annex-file").click());
byId("annex-file").addEventListener("change",event => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file) importFile(file);
});
const drop = byId("annex-drop");
drop.addEventListener("dragover",event => { event.preventDefault(); drop.classList.add("drag-over"); });
drop.addEventListener("dragleave",() => drop.classList.remove("drag-over"));
drop.addEventListener("drop",event => {
  event.preventDefault();
  drop.classList.remove("drag-over");
  if (event.dataTransfer.files.length === 1) importFile(event.dataTransfer.files[0]);
  else annexStatus("Elegí un solo archivo .xlsx.",true);
});
for (const [id,kind] of [["add-cheque","cheques"],["add-installment","installments"]]) {
  byId(id).addEventListener("click",() => {createRow(kind,{},true); changed = true; refresh();});
}
for (const eventType of ["input","change"]) {
  form.addEventListener(eventType,event => {
    if (event.target !== control("combined_document")) changed = true;
    if (event.target === control("bank_principal")) importedPrincipal = null;
    statusMessage("");
    refresh();
  });
}

function issue(message, page, field) {
  showPage(page,false,false);
  statusMessage(message,true);
  field?.focus();
}

async function verifyService() {
  const response = await fetch("/api/health", {cache:"no-store"});
  if (!response.ok) throw new Error("No se pudo verificar el servicio.");
  const health = await response.json();
  templateService = health.service === "panel-documental" && health.annex_import === true;
  combinedService = health.combined_document === true;
  const pdfAvailable = health.pdf_export ?? localPanel;
  const formatOptions = byId("format-options");
  formatOptions.hidden = !pdfAvailable;
  for (const radio of formatOptions.querySelectorAll("md-radio")) {
    radio.disabled = !pdfAvailable && radio.value !== "docx";
    if (!pdfAvailable) radio.checked = radio.value === "docx";
  }
  refresh();
  return templateService && (!checked("combined_document") || combinedService);
}

function restoreOperation(saved) {
  for (const box of form.querySelectorAll('md-checkbox[name="documents"]')) box.checked = saved.documents.includes(box.value);
  for (const radio of form.querySelectorAll('md-radio[name="format"]')) radio.checked = radio.value === saved.format;
  for (const key of ["place","issue_date","currency","net","penalty","payment_address","note_mode",
    "demand_payee","demand_id","demand_amount","demand_consideration",...bankFields]) {
    if (saved[key] != null) writeField(control(key), saved[key]);
  }
  for (const key of ["represented","guarantor_enabled","different_beneficiary","different_assignee","separate_payment","combined_document"]) {
    control(key).selected = Boolean(saved[key]);
  }
  control("linked").selected = saved.installments_mode === "cheques";
  if (saved.cheque_net != null) control("net").value = saved.cheque_net;
  for (const key of ["client","entity","representative","beneficiary","assignee","guarantor"]) {
    if (saved[key]) for (const part of ["name","id_type","id_number","address"]) control(key+"."+part).value = saved[key][part];
  }
  for (const kind of ["cheques","installments"]) {
    byId(kind).replaceChildren();
    if (!saved[kind].length) createRow(kind);
    else for (const row of saved[kind]) createRow(kind,row);
  }
  changed = true;
  refresh();
}

byId("open-service").addEventListener("click",() => {
  if (!companion) return;
  const next = new URL(companion);
  next.hash = "continuar";
  migrationWindow = window.open(next.href,"panel_modelos");
  if (!migrationWindow) statusMessage("Permití abrir la pestaña para conservar la carga.",true);
});

window.addEventListener("message", event => {
  if (!companion || event.origin !== companion.origin) return;
  if (event.source === migrationWindow && event.data?.type === "panel-ready") {
    migrationWindow.postMessage({type:"panel-operation",operation:payload(),page:current},companion.origin);
  } else if (location.hash === "#continuar" && event.source === window.opener && event.data?.type === "panel-operation") {
    restoreOperation(event.data.operation);
    showPage(Math.max(0,Math.min(2,Number(event.data.page) || 0)),false,false);
    history.replaceState(null,"",location.pathname);
  }
});

form.addEventListener("submit",async event => {
  event.preventDefault();
  if (busy || importing) return;
  refresh();
  try {
    if (!await verifyService()) return;
  } catch {
    statusMessage("No se pudo verificar el servicio. Volvé a intentar.",true);
    return;
  }
  const data = payload(supported(selectedDocs()));
  if (!data.documents.length) {
    issue("Elegí al menos un documento.",0,form.querySelector('[name="documents"]'));
    return;
  }
  const f = flagsFor(data.documents);
  for (const field of form.querySelectorAll(editable)) {
    if (field.disabled || field.closest("[data-removing]")) continue;
    if (!field.validity.valid) {
      const page = field.closest('[role="tabpanel"]');
      showPage(page ? pages.indexOf(page.id) : 0,false,false);
      statusMessage("");
      fieldError(field,validationMessage(field));
      field.focus();
      return;
    }
  }
  if (f.needCheques && !data.cheques.length) { issue("Agregá al menos un cheque.",2,byId("add-cheque")); return; }
  if (f.independent && !data.installments.length) { issue("Agregá al menos una cuota.",2,byId("add-installment")); return; }
  busy = true;
  byId("generate").disabled = true;
  statusMessage("Preparando la descarga…");
  try {
    const response = await fetch("/api/generate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "No se pudo generar la descarga.");
    }
    const blob = await response.blob();
    const path = URL.createObjectURL(blob);
    const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const link = element("a",{href:path,download:match ? match[1] : "documentos.zip"});
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(path),15000);
    statusMessage("Descarga iniciada.");
  } catch (error) {
    statusMessage(error.message === "Failed to fetch" ? "No hay conexión con el panel. Volvé a intentar." : error.message,true);
  } finally {
    busy = false;
    refresh();
  }
});

enableNumericDate(control("issue_date"));
writeField(control("issue_date"), localDate());
createRow("cheques");
createRow("installments");
showPage(0);
refresh();
Promise.all([...form.querySelectorAll("md-outlined-select")].map(node => node.updateComplete)).then(async () => {
  refresh();
  if (await verifyService() && companion && location.hash === "#continuar" && window.opener) {
    window.opener.postMessage({type:"panel-ready"},companion.origin);
  }
}).catch(() => statusMessage("No se pudo verificar el servicio. Volvé a intentar.",true));
