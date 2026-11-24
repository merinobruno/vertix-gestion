import assert from "node:assert/strict";
import test from "node:test";
import {boundedInsertion, displayNumber} from "../src/identification.js";

test("a paste into an empty field uses the first available digits, including leading zeroes", () => {
  const tax = boundedInsertion("", 0, 0, "CUIT 00-12345678-90123", 11);
  assert.equal(displayNumber("CUIT/CUIL", tax.digits), "00-12345678-9");
  const dni = boundedInsertion("", 0, 0, "DNI 00.123.456789", 8);
  assert.equal(displayNumber("DNI", dni.digits), "00.123.456");
});

test("insertion in the middle of a full number keeps all unselected digits", () => {
  assert.deepEqual(boundedInsertion("20-12345678-9", 5, 5, "9", 11), {digits:"20123456789", start:4, end:4});
  assert.deepEqual(boundedInsertion("20-12345678-9", 5, 7, "99 123", 11), {digits:"20129956789", start:6, end:6});
  assert.deepEqual(boundedInsertion("00.123.456", 3, 6, "98-76", 8), {digits:"00987456", start:5, end:5});
});

test("arbitrary text and punctuation cannot displace a selected number", () => {
  assert.deepEqual(boundedInsertion("00.123.456", 3, 6, " - letras . ", 8), {digits:"00123456", start:2, end:5});
  assert.deepEqual(boundedInsertion("00.123.456", 3, 6, "", 8), {digits:"00456", start:2, end:2});
});
