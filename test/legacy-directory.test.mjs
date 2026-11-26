import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import {IDBFactory, IDBKeyRange} from "fake-indexeddb";
import {legacyPartyCount, legacyPartyPage} from "../src/legacy-directory.js";
import {fieldsFromReplacedParty, reconcileDirectoryDraft} from "../src/party-directory.js";

beforeEach(() => { globalThis.indexedDB = new IDBFactory(); globalThis.IDBKeyRange = IDBKeyRange; });

test("checking for an import does not create a new browser directory", async () => {
  assert.equal(await legacyPartyCount(), 0);
  assert.deepEqual(await indexedDB.databases(), []);
});

test("import reads all existing profiles in batches without changing their original records", async () => {
  const open = indexedDB.open("vertix-agenda", 1);
  open.onupgradeneeded = () => open.result.createObjectStore("parties", {keyPath:"id",autoIncrement:true});
  const db = await new Promise(resolve => { open.onsuccess = () => resolve(open.result); });
  const tx = db.transaction("parties", "readwrite");
  const original = Array.from({length:27}, (_, n) => ({name:"Fixture " + n, id_type:"DNI", id_number:String(n + 1), address:{}}));
  for (const profile of original) tx.objectStore("parties").add({profile, revision:1});
  await new Promise(resolve => { tx.oncomplete = resolve; });
  db.close();
  assert.equal(await legacyPartyCount(), original.length);
  const first = await legacyPartyPage(null), second = await legacyPartyPage(first.lastId);
  assert.equal(first.parties.length, 25);
  assert.deepEqual([...first.parties, ...second.parties], original);
  assert.equal(await legacyPartyCount(), 27);
});

test("a changed lookup discards the previous identity and only its untouched autofill", () => {
  const address = {street:"Calle Ejemplo", number:"3", floor:"2", apartment:"A", locality:"Ciudad", province:"Provincia"};
  const previous = {name:"Persona uno", id_type:"DNI", id_number:"00.123.456", address};
  const different = {...previous, id_number:"00.123.457", address:{...address, floor:"1"}};
  assert.deepEqual(fieldsFromReplacedParty(previous, different, false),
    ["name", "address.street", "address.number", "address.apartment", "address.locality", "address.province"]);
  assert.deepEqual(fieldsFromReplacedParty(previous, {...different, name:"Nueva búsqueda"}, true),
    ["id_number", "address.street", "address.number", "address.apartment", "address.locality", "address.province"]);
  assert.ok(fieldsFromReplacedParty(previous, {...previous, id_type:"CUIT/CUIL"}, false).includes("name"));
  assert.equal(fieldsFromReplacedParty(previous, {...previous, id_number:"00123456"}, false), null);
  const tax = {...previous, id_type:"CUIL", id_number:"00-12345678-9"};
  assert.equal(fieldsFromReplacedParty(tax, {...tax, id_type:"CUIT/CUIL"}, false), null);
});

test("agenda saves update selected contact fields while preserving independent operation edits", () => {
  const contact = {name:"Fixture", id_type:"DNI", id_number:"00.000.001", address:{street:"Calle Beta", number:"10"}};
  const before = {...contact, borrower_representative:{...contact, name:"Representante"}};
  const draft = {...before, address:{...before.address, number:"17"},
    borrower_representative:{...before.borrower_representative, name:"Representante en borrador"}};
  const saved = {...before, address:{...before.address, street:"Calle Corregida Beta"},
    borrower_representative:{...before.borrower_representative, address:{street:"Nueva calle", number:"5"}}};
  const {party, conflicts} = reconcileDirectoryDraft(before, draft, saved, "client");
  assert.deepEqual(conflicts, []);
  assert.equal(party.address.street, "Calle Corregida Beta");
  assert.equal(party.address.number, "17");
  assert.equal(party.borrower_representative.name, "Representante en borrador");
  assert.equal(party.borrower_representative.address.street, "Nueva calle");
});

test("a real conflict offers both values without merging different representatives' identities", () => {
  const contact = {name:"Fixture", id_type:"DNI", id_number:"1", address:{street:"Original", number:"1"}};
  const before = {...contact, lender_representative:{...contact, title:"el Sr."}};
  const draft = {...before, address:{street:"Borrador", number:"7"},
    lender_representative:{...before.lender_representative, name:"Anterior corregido"}};
  const saved = {...before, address:{street:"Agenda", number:"1"},
    lender_representative:{name:"Nueva persona", id_type:"DNI", id_number:"2", title:"la Sra."}};
  for (const preferSaved of [false, true]) {
    const {party, conflicts} = reconcileDirectoryDraft(before, draft, saved, "entity", preferSaved);
    assert.deepEqual(conflicts, ["Domicilio · Calle", "Representante"]);
    assert.equal(party.address.street, preferSaved ? "Agenda" : "Borrador");
    assert.equal(party.address.number, "7");
    assert.deepEqual(party.lender_representative, preferSaved ? saved.lender_representative : draft.lender_representative);
  }
});

test("beneficiary and assignee update only the parts of a shared profile used in their form", () => {
  const previous = {name:"Ficha", id_type:"DNI", id_number:"123", address:{street:"Original", number:"2"},
    lender_representative:{name:"Anterior", id_type:"DNI", id_number:"5"}};
  const saved = {...previous, name:"Ficha corregida", address:{street:"Corregida", number:"2"},
    lender_representative:{name:"Siguiente", id_type:"DNI", id_number:"7"}};
  const identity = {name:previous.name, id_type:"DNI", id_number:"123"};
  for (const role of ["beneficiary", "assignee", "demand"]) {
    const draft = role === "beneficiary" ? {...identity, address:{street:"Original", number:"8"}} : identity;
    const {party, conflicts} = reconcileDirectoryDraft(previous, draft, saved, role);
    assert.deepEqual(conflicts, []);
    assert.equal(party.name, "Ficha corregida");
    assert.deepEqual(party.address, role === "beneficiary" ? {street:"Corregida", number:"8", floor:"", apartment:"", locality:"", province:""} : saved.address);
    assert.deepEqual(party.lender_representative, saved.lender_representative);
    assert.deepEqual(fieldsFromReplacedParty(previous, {...identity, name:"Otra"}, true), ["id_number"]);
  }
});
