// A spreadsheet cell may include Argentine grouping, a currency symbol or a percent sign.
// Keep the digits as text until validation is complete so a bad paste cannot change the amount.
export function decimalValue(raw, kind, currency = "ARS") {
  let text = String(raw).trim().replace(/[\u00a0\u202f]/g, " ");
  if (kind === "money") {
    const prefix = /^(?:(ARS|USD) *\$?|(US\$|U\$S)|\$) */i.exec(text);
    if (prefix) {
      const code = prefix[1]?.toUpperCase() || (prefix[2] ? "USD" : currency);
      if (code !== currency) return null;
      text = text.slice(prefix[0].length);
    }
  }
  if (kind === "percent") text = text.replace(/ *%$/, "");
  if (/^[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]{1,2})?$/.test(text)) {
    text = text.replaceAll(".", "");
  } else if (/^[0-9]{1,3}(?: [0-9]{3})+(?:[.,][0-9]{1,2})?$/.test(text)) {
    text = text.replaceAll(" ", "");
  } else if (/^[0-9]{1,3}(?:,[0-9]{3})+\.[0-9]{1,2}$/.test(text)) {
    text = text.replaceAll(",", "");
  }
  text = text.replace(",", ".").replace(/^\./, "0.");
  return /^[0-9]{1,12}(?:\.[0-9]{1,2})?$/.test(text) ? text : null;
}

export function displayDecimal(raw, kind, currency) {
  const value = decimalValue(raw, kind, currency);
  return value === null ? String(raw) : value.replace(".", ",");
}
