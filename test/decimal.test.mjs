import assert from "node:assert/strict";
import {test} from "node:test";
import {decimalValue, displayDecimal} from "../src/decimal.js";

test("Excel amounts keep their cents and Argentine thousands separators", () => {
  for (const [raw, expected] of [
    ["$ 1.234.567,89", "1234567.89"], ["1.234", "1234"], ["1234,5", "1234.5"], [",05", "0.05"],
    ["12 345,67", "12345.67"], ["1234.56", "1234.56"], ["1,234.56", "1234.56"], ["0,00", "0.00"],
    ["999.999.999.999,99", "999999999999.99"],
  ]) {
    assert.equal(decimalValue(raw, "money"), expected);
    assert.equal(decimalValue(displayDecimal(raw, "money"), "money"), expected);
  }
  assert.equal(decimalValue("12,50 %", "percent"), "12.50");
  assert.equal(decimalValue("ARS $ 1.234,56", "money", "ARS"), "1234.56");
  assert.equal(decimalValue("US$ 1.234,56", "money", "USD"), "1234.56");
  assert.equal(decimalValue("ARS 1.234,56", "money", "USD"), null);
  assert.equal(displayDecimal("1234.56", "money"), "1234,56");
});

test("malformed data, extra precision and out-of-range amounts are rejected intact", () => {
  for (const raw of ["1.23,45", "1,234", "2,3,4", "1\t2", "1e4", "-10", "3%", "1.000.000.000.000,00"]) {
    assert.equal(decimalValue(raw, "money"), null);
    assert.equal(displayDecimal(raw, "money"), raw);
  }
  assert.equal(decimalValue("$ 5", "percent"), null);
});
