import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import {IDBFactory, IDBKeyRange} from "fake-indexeddb";
import {legacyPartyCount, legacyPartyPage} from "../src/legacy-directory.js";
import {fieldsFromReplacedParty} from "../src/party-directory.js";

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
