import {addressFields, addressLine} from "./address.js";
import {displayNumber, documentDigits, identificationGroup, numberProblem, taxKind} from "./identification.js";

const pageSize = 25;
class DirectoryError extends Error {}

function textField(value, label, required = true) {
  const text = typeof value === "string" ? value.trim().replace(/\s+/gu, " ") : "";
  if (required && !text) throw new DirectoryError("Completá " + label + ".");
  if ([...text].length > 240 || /[\u0000-\u001f\ue100-\ue1ff\ufffe\uffff\ud800-\udfff]/u.test(text)) {
    throw new DirectoryError("Revisá " + label + ".");
  }
  return text;
}

function searchKey(text) {
  return text.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
}

function validatedProfile(party, editing) {
  const name = textField(party?.name, "el nombre o la razón social");
  if ([...name].length > 180) throw new DirectoryError("Usá hasta 180 caracteres en el nombre.");
  const number = textField(party.id_number, "el número de identificación");
  const problem = numberProblem(party.id_type, number);
  if (problem) throw new DirectoryError(problem);
  const profile = {name, id_type:party.id_type, id_number:displayNumber(party.id_type, number)};
  if (!Object.hasOwn(party, "address")) return profile;
  if (!party.address || typeof party.address !== "object" || Array.isArray(party.address)) {
    throw new DirectoryError("Completá el domicilio por campos.");
  }
  const address = Object.fromEntries(Object.keys(addressFields).map(key => [key, textField(party.address[key], "el domicilio", false)]));
  if (editing && !Object.values(address).some(Boolean)) {
    profile.address = {};
  } else {
    if (!address.street || !address.number || !address.locality || !address.province) {
      throw new DirectoryError("Completá el domicilio con calle, altura, localidad y provincia.");
    }
    if ([...addressLine(address)].length > 240) throw new DirectoryError("El domicilio completo supera los 240 caracteres.");
    profile.address = address;
  }
  return profile;
}

function requestValue(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function openDirectory() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("vertix-agenda", 1);
    let blocked = false;
    open.onupgradeneeded = () => {
      const parties = open.result.createObjectStore("parties", {keyPath:"id", autoIncrement:true});
      parties.createIndex("document", ["kind", "digits"], {unique:true});
      parties.createIndex("name", "nameKey");
    };
    open.onblocked = () => {
      blocked = true;
      reject(new DirectoryError("Cerrá las otras pestañas del panel y volvé a abrir la Agenda."));
    };
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      if (blocked) { db.close(); return; }
      db.onversionchange = () => db.close();
      resolve(db);
    };
  });
}

async function transaction(mode, action, signal) {
  let db;
  try {
    signal?.throwIfAborted();
    db = await openDirectory();
    signal?.throwIfAborted();
    const tx = db.transaction("parties", mode);
    const completed = new Promise((resolve, reject) => {
      tx.oncomplete = resolve;
      tx.onabort = () => reject(tx.error || new Error("Transaction aborted"));
    });
    const abort = () => { try { tx.abort(); } catch { /* Already settled. */ } };
    signal?.addEventListener("abort", abort, {once:true});
    try {
      // A successful request is provisional until its transaction commits.
      const [result] = await Promise.all([action(tx.objectStore("parties")), completed]);
      return result;
    } catch (error) {
      abort();
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
    }
  } catch (error) {
    signal?.throwIfAborted();
    if (error instanceof DirectoryError) throw error;
    if (error?.name === "QuotaExceededError") throw new DirectoryError("No hay espacio en este navegador para guardar la ficha.");
    throw new DirectoryError("No se pudo acceder a la Agenda. Permití el almacenamiento de este sitio en el navegador y volvé a intentar.");
  } finally {
    db?.close();
  }
}

function asParty(record) {
  return {id:record.id, revision:record.revision, ...record.profile};
}

async function writeParty(store, profile, payload, editing) {
  const kind = identificationGroup(profile.id_type);
  const digits = documentDigits(profile.id_number);
  const sameDocument = await requestValue(store.index("document").get([kind, digits]));
  const existing = editing ? await requestValue(store.get(payload.id)) : sameDocument;
  if (editing) {
    if (!existing) throw new DirectoryError("Ya no se encuentra esta ficha en la agenda.");
    if (existing.revision !== payload.revision) {
      throw new DirectoryError("La ficha cambió desde que la abriste. Volvé a abrirla para revisar sus datos actuales.");
    }
    if (sameDocument && sameDocument.id !== existing.id) {
      throw new DirectoryError("Ese tipo y número de identificación ya corresponden a otra ficha.");
    }
  } else if (existing && payload.create_only) {
    throw new DirectoryError("Ese documento ya está guardado. Seleccioná la ficha para actualizarla.");
  }
  if (sameDocument && kind === taxKind) profile.id_type = sameDocument.profile.id_type;
  profile.address ??= existing?.profile.address || {};
  if (existing && !editing) {
    if (JSON.stringify(existing.profile) === JSON.stringify(profile)) return {party:asParty(existing), created:false};
    throw new DirectoryError("Ese documento ya está guardado. Volvé a seleccionar la ficha antes de actualizarla.");
  }
  const record = {kind, digits, nameKey:searchKey(profile.name), profile, revision:(existing?.revision || 0) + 1};
  if (existing) record.id = existing.id;
  record.id = await requestValue(store.put(record));
  return {party:asParty(record), created:!existing};
}

function visitByName(store, visit, offset = 0) {
  return new Promise((resolve, reject) => {
    const request = store.index("name").openCursor();
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) { resolve(); return; }
      if (offset) { cursor.advance(offset); offset = 0; return; }
      if (visit(cursor.value) === false) resolve();
      else cursor.continue();
    };
  });
}

async function matches(store, query, offset, limit) {
  if (!query) {
    const parties = [];
    const [total] = await Promise.all([requestValue(store.count()), visitByName(store, row => {
      parties.push(asParty(row));
      return parties.length < limit;
    }, offset)]);
    return {parties, total, offset, page_size:pageSize};
  }
  const identifier = /^(?:(dni|cuit\/cuil|cuit|cuil)[:#]?)?([0-9]+)$/.exec(documentDigits(query));
  const kind = identificationGroup(identifier?.[1]?.toUpperCase());
  const digits = identifier?.[2];
  const byRank = [[], [], []];
  let total = 0;
  await visitByName(store, row => {
    const numberMatches = digits && (!kind || kind === row.kind) && row.digits.includes(digits);
    if (!row.nameKey.includes(query) && !numberMatches) return;
    let rank = 2;
    if (numberMatches && row.digits === digits) rank = 0;
    else if (row.nameKey.startsWith(query) || (numberMatches && row.digits.startsWith(digits))) rank = 1;
    total++;
    if (byRank[rank].length < offset + limit) byRank[rank].push(asParty(row));
  });
  return {parties:byRank.flat().slice(offset, offset + limit), total, offset, page_size:pageSize};
}

export async function browserDirectoryRequest(action, payload, signal) {
  if (action === "list" || action === "search") {
    const offset = action === "list" ? payload.offset ?? 0 : 0;
    if (!Number.isInteger(offset) || offset < 0 || offset > 2147483647) throw new DirectoryError("La página de la agenda no es válida.");
    if (typeof payload.query === "string" && payload.query.length > 240) throw new DirectoryError("La búsqueda admite hasta 240 caracteres.");
    const query = searchKey(textField(payload.query, "la búsqueda", false));
    if (action === "search" && !query) return {parties:[]};
    return transaction("readonly", store => matches(store, query, offset, action === "list" ? pageSize : 8), signal);
  }
  const editing = action === "update";
  if (!editing && action !== "save") throw new DirectoryError("La acción de la agenda no es válida.");
  if (editing && (!Number.isSafeInteger(payload.id) || payload.id <= 0 || !Number.isSafeInteger(payload.revision) || payload.revision <= 0)) {
    throw new DirectoryError("Volvé a abrir la ficha antes de editarla.");
  }
  const profile = validatedProfile(payload.party, editing);
  return transaction("readwrite", store => writeParty(store, profile, payload, editing));
}
