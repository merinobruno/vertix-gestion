import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";
import {IDBFactory} from "fake-indexeddb";
import {browserDirectoryRequest as directory} from "../src/browser-directory.js";
import {fieldsFromReplacedParty} from "../src/party-directory.js";

const address = {street:"Calle Ejemplo", number:"3", floor:"2", apartment:"A", locality:"Ciudad", province:"Provincia"};
const person = (name, number) => ({name, id_type:"DNI", id_number:number, address});
const save = party => directory("save", {party, create_only:true});
const list = (query = "", offset = 0) => directory("list", {query, offset});
const search = (query, role) => directory("search", {query, role});
const edit = (selected, party) => directory("update", {id:selected.id, revision:selected.revision, party});

beforeEach(() => { globalThis.indexedDB = new IDBFactory(); });

test("a browser starts empty and both roles reopen the same saved parties by name or document", async () => {
  const originalStorage = indexedDB;
  assert.equal((await list()).total, 0);
  const tax = {name:"Álvarez S.A.", id_type:"CUIL", id_number:"00-00123456-7", address};
  const {party} = await directory("save", {party:tax, role:"client", create_only:true});
  await save(person("Álvarez S.A.", "123456"));
  assert.deepEqual((await search("CUIT/CUIL: 00.001.234.567", "entity")).parties, [party]);
  assert.deepEqual((await search("cuit#12.345-6", "entity")).parties, [party]);
  assert.equal((await search("alVarEz", "entity")).parties.length, 2);
  assert.equal((await list("DNI 123.456")).parties[0].id_type, "DNI");
  const duplicate = {...tax, id_type:"CUIT/CUIL", id_number:"00001234567"};
  await assert.rejects(save(duplicate), /ya está guardado/);
  assert.equal((await edit(party, duplicate)).party.id_type, "CUIL");
  globalThis.indexedDB = new IDBFactory();
  assert.equal((await list()).total, 0);
  globalThis.indexedDB = originalStorage;
  assert.equal((await list()).total, 2);
});

test("concurrent creates and updates commit atomically, retain the stable ID and reject collisions", async () => {
  const first = person("Persona de prueba", "00123456");
  const creates = await Promise.allSettled([save(first), save(first)]);
  assert.equal(creates.filter(result => result.status === "fulfilled").length, 1);
  const [selected] = (await list()).parties;
  const other = (await save(person("Mismo nombre", "00123457"))).party;
  await assert.rejects(edit(selected, {...first, id_number:other.id_number}), /otra ficha/);
  const updates = await Promise.allSettled([
    edit(selected, {...first, name:"Primera edición", id_number:"12"}),
    edit(selected, {...first, name:"Otra edición", id_number:"13"}),
  ]);
  assert.equal(updates.filter(result => result.status === "fulfilled").length, 1);
  assert.match(updates.find(result => result.status === "rejected").reason.message, /cambió/);
  const edited = updates.find(result => result.status === "fulfilled").value.party;
  assert.equal(edited.id, selected.id);
  assert.notEqual(edited.revision, selected.revision);
  assert.deepEqual((await list("DNI " + other.id_number)).parties, [other]);
  assert.equal((await list("DNI 00-123-456")).total, 0);
});

test("directory limits, address validation, and hidden address handling match the operation", async () => {
  const {party} = await save(person("Nombre", "7"));
  const hidden = {name:"Nombre editado", id_type:"DNI", id_number:"7"};
  assert.deepEqual((await edit(party, hidden)).party.address, address);
  await assert.rejects(save({...person("X", "8"), address:{street:"Calle"}}), /domicilio/);
  await assert.rejects(save({...person("X", "8"), address:{...address, street:"a".repeat(235)}}), /240/);
  await assert.rejects(save(person("X", "123456789")), /entre 1 y 8/);
  await assert.rejects(save({...person("X", "8"), id_type:"CUIT/CUIL"}), /11 dígitos/);
  await assert.rejects(save(person("x".repeat(181), "8")), /180/);
  await assert.rejects(list("x".repeat(241)), /240/);
  await assert.rejects(list("", -1), /página/);
  const noAddress = await save({...hidden, id_number:"9"});
  assert.deepEqual(noAddress.party.address, {});
  assert.deepEqual((await edit(noAddress.party, {...hidden, id_number:"9", address:{}})).party.address, {});
  assert.equal((await list()).total, 2);
});

test("the Agenda covers every match and sorts exact documents and name prefixes ahead of substrings", async () => {
  await Promise.all(Array.from({length:30}, (_, n) => save(person("Grupo " + String(n).padStart(2, "0"), String(300 + n)))));
  await save(person("A Grupo", "30"));
  const first = await list("grupo");
  const second = await list("grupo", first.page_size);
  assert.equal(first.total, 31);
  assert.equal(first.parties.length, 25);
  assert.equal(second.parties.at(-1).name, "A Grupo");
  assert.equal(new Set([...first.parties, ...second.parties].map(row => row.id)).size, 31);
  assert.equal((await list("", 25)).parties.length, 6);
  const suggestions = await search("30");
  assert.equal(suggestions.parties.length, 8);
  assert.equal(suggestions.parties[0].id_number, "30");
});

test("disabled storage reports a failure and cancelled searches do not produce results", async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(directory("list", {}, controller.signal), {name:"AbortError"});
  globalThis.indexedDB = undefined;
  await assert.rejects(list(), /Permití el almacenamiento/);
  await assert.rejects(save(person("Nombre", "7")), /Permití el almacenamiento/);
});

test("a changed party lookup discards the previous identity and only its untouched autofill", () => {
  const previous = person("Persona uno", "00.123.456");
  const different = {...previous, id_number:"00.123.457", address:{...address, floor:"1"}};
  assert.deepEqual(fieldsFromReplacedParty(previous, different, false),
    ["name", "address.street", "address.number", "address.apartment", "address.locality", "address.province"]);
  assert.deepEqual(fieldsFromReplacedParty(previous, {...different, name:"Nueva búsqueda"}, true),
    ["id_number", "address.street", "address.number", "address.apartment", "address.locality", "address.province"]);
  assert.ok(fieldsFromReplacedParty(previous, {...previous, id_type:"CUIT/CUIL"}, false).includes("name"));
  assert.equal(fieldsFromReplacedParty(previous, {...previous, id_number:"00123456"}, false), null);
  const previousTax = {...previous, id_type:"CUIL", id_number:"00-12345678-9"};
  assert.equal(fieldsFromReplacedParty(previousTax, {...previousTax, id_type:"CUIT/CUIL"}, false), null);
});
