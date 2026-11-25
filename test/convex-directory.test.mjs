import assert from "node:assert/strict";
import {randomBytes} from "node:crypto";
import {beforeEach, test} from "node:test";
import {convexTest} from "convex-test";
import schema from "../convex/schema.js";
import * as storage from "../convex/directory.js";

const modules = {"./_generated/api.js":() => import("convex/server"), "./directory.js":() => import("../convex/directory.js"),
  "./http.js":() => import("../convex/http.js")};
let db, key;
beforeEach(() => {
  db = convexTest(schema, modules);
  key = randomBytes(32).toString("hex");
  process.env.VERTIX_AGENDA_SERVICE_KEY = key + "\n";
});

async function post(action, data, bearer = key) {
  const response = await db.fetch("/agenda", {method:"POST", headers:{"Authorization":"Bearer " + bearer,"Content-Type":"application/json"},
    body:JSON.stringify({action, data})});
  return {status:response.status, body:await response.json()};
}
function record(n, name = "Fixture") {
  return {kind:"DNI", digits:String(n), nameKey:name.toLowerCase(), profile:{name, id_type:"DNI", id_number:String(n)}};
}
const listing = (name = "", digits = "", kind = "", offset = 0) => post("list", {name, digits, kind, offset});

test("only internal CRUD functions exist and the HTTP action requires the service credential", async () => {
  for (const fn of Object.values(storage)) assert.equal(fn.isInternal, true);
  assert.equal((await post("list", {}, "")).status, 403);
  assert.equal((await post("save", {record:record(1)}, "wrong")).status, 403);
  assert.equal((await listing()).body.total, 0);
});

test("competing creates and edits preserve document uniqueness and revision checks", async () => {
  const r = record(1);
  const results = await Promise.all([post("save", {record:r}), post("save", {record:r})]);
  assert.deepEqual(results.map(x => x.status).sort(), [200,409]);
  const original = results.find(x => x.status === 200).body.party;
  const other = (await post("save", {record:record(2)})).body.party;
  const edit = entry => post("update", {id:original.id, revision:original.revision, record:entry});
  assert.equal((await edit(record(2))).status, 409);
  const writes = await Promise.all([edit(record(3)), edit(record(4))]);
  assert.deepEqual(writes.map(x => x.status).sort(), [200,409]);
  assert.equal(writes.find(x => x.status === 200).body.party.id, original.id);
  assert.deepEqual((await listing("dni2", "2", "DNI")).body.parties, [other]);
  assert.equal((await listing()).body.total, 2);
});

test("tax aliases share an identity, retain the chosen legacy label and preserve a hidden address", async () => {
  const address = {street:"Ejemplo",number:"1",locality:"Ciudad",province:"Provincia"};
  const record = {kind:"CUIT/CUIL",digits:"00123456789",nameKey:"ficcion",profile:{name:"Ficción",id_type:"CUIL",id_number:"00-12345678-9",address}};
  const {party} = (await post("save", {record})).body;
  const changed = {...record, profile:{...record.profile,id_type:"CUIT/CUIL"}};
  delete changed.profile.address;
  assert.equal((await post("save", {record:changed})).status, 409);
  const updated = await post("update", {record:changed,id:party.id,revision:party.revision});
  assert.equal(updated.body.party.id_type, "CUIL");
  assert.deepEqual(updated.body.party.address, address);
  assert.equal((await listing("cuit 00", "00", "CUIT/CUIL")).body.total, 1);
});

test("explicit import is repeatable even after a shared ID changes and never overwrites conflicts", async () => {
  const a = {...record(1),fingerprint:"a".repeat(64)}, b = {...record(2),fingerprint:"b".repeat(64)};
  await post("save", {record:record(2,"Existing")});
  const first = await post("import", {records:[a,b]});
  assert.deepEqual(first.body, {created:1,existing:0,conflicts:1});
  const [selected] = (await listing("dni1","1","DNI")).body.parties;
  await post("update", {id:selected.id,revision:selected.revision,record:record(3)});
  assert.deepEqual((await post("import", {records:[a,b]})).body, {created:0,existing:1,conflicts:1});
  assert.equal((await listing()).body.total, 2);
  assert.equal((await listing("dni1","1","DNI")).body.total, 0);
});

test("a page beyond the first internal batch covers all matches in rank and name order", async () => {
  await db.run(async ctx => {
    for (let n = 0; n < 132; n++) await ctx.db.insert("parties", {...record(1000+n,"Grupo " + String(n).padStart(3,"0")),revision:1});
    await ctx.db.insert("parties", {...record(10,"A Grupo"),revision:1});
  });
  const page = (await listing("grupo","","",125)).body;
  assert.equal(page.total,133);
  assert.equal(page.parties.length,8);
  assert.equal(page.parties.at(-1).name,"A Grupo");
  const result = await post("search", {name:"10",digits:"10",kind:"",offset:0});
  assert.equal(result.body.parties.length,8);
  assert.equal(result.body.parties[0].id_number,"10");
});
