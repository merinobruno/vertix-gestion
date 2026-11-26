import assert from "node:assert/strict";
import test from "node:test";
import {bindIdentification, boundedInsertion, displayNumber, identificationProblem, readIdentification} from "../src/identification.js";

test("single formatted identities retain all digits, including leading zeroes", () => {
  const tax = boundedInsertion("", 0, 0, "00-12345678-9", 11);
  assert.equal(displayNumber("CUIT/CUIL", tax.digits), "00-12345678-9");
  const dni = boundedInsertion("", 0, 0, "00.123.456", 8);
  assert.equal(displayNumber("DNI", dni.digits), "00.123.456");
});

test("edits reject the entire overflow and retain unselected digits on a valid replacement", () => {
  assert.equal(boundedInsertion("20-12345678-9", 5, 5, "9", 11), null);
  assert.equal(boundedInsertion("20-12345678-9", 5, 7, "99 123", 11), null);
  assert.deepEqual(boundedInsertion("00.123.456", 3, 6, "98-7", 8), {digits:"00987456", start:5, end:5});
});

test("multiple cells, a tax number in DNI and arbitrary text cannot replace an identity", () => {
  for (const text of ["7\t00-74012345-6", "7\n1234567890", "00-74012345-6", " - letras . "]) {
    assert.equal(boundedInsertion("00.123.456", 0, 10, text, 8), null);
  }
  assert.equal(boundedInsertion("", 0, 0, "7\t00-74012345-6", 11), null);
  assert.deepEqual(boundedInsertion("00.123.456", 3, 6, "", 8), {digits:"00456", start:2, end:2});
});

test("a valid preformatted CUIT survives clearing the previous paste error on a Material field", t => {
  const original = globalThis.HTMLInputElement;
  globalThis.HTMLInputElement = class {};
  t.after(() => {
    if (original) globalThis.HTMLInputElement = original;
    else delete globalThis.HTMLInputElement;
  });
  const input = Object.assign(new HTMLInputElement(), {value:"", selectionStart:0, selectionEnd:0});
  const listeners = {};
  const host = {value:"", addEventListener:(name, fn) => { listeners[name] = fn; }, closest:() => new EventTarget()};
  const type = Object.assign(new EventTarget(), {value:"CUIT/CUIL"});
  bindIdentification(type, host, type.value, () => { input.value = host.value; });
  const paste = text => {
    let prevented = false;
    listeners.paste({composedPath:() => [input], clipboardData:{getData:() => text}, preventDefault:() => { prevented = true; }});
    return prevented;
  };
  assert.equal(paste("7\t00-85012345-6"), true);
  assert.match(identificationProblem(host), /sola identificación/);
  const corrected = "00-85012345-6";
  assert.equal(paste(corrected), false);
  const event = {inputType:"insertFromPaste", composedPath:() => [input]};
  listeners.beforeinput(event);
  input.value = corrected;
  input.selectionStart = input.selectionEnd = corrected.length;
  listeners.input(event);
  assert.equal(input.value, corrected);
  assert.equal(identificationProblem(host), "");
  assert.equal(readIdentification(host).id_number, corrected);
});
