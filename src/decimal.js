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

export function minorUnits(text) {
  const match = /^(\d{1,12})(?:\.(\d{1,2}))?$/.exec(text);
  return match ? BigInt(match[1])*100n + BigInt((match[2] || "").padEnd(2,"0")) : null;
}

export function breakdownProblem(rows, principal) {
  if (!rows.some(row => row.capital || row.interest || row.vat)) return null;
  let totalCapital = 0n;
  for (const [index, row] of rows.entries()) {
    const [amount, capital, interest, vat] = [row.amount, row.capital, row.interest, row.vat].map(minorUnits);
    if ([amount, capital, interest, vat].includes(null)) return null; // Individual fields report invalid amounts.
    if (capital + interest + vat !== amount) {
      return {index, field:"amount", message:"El importe de la cuota " + (index+1) + " debe coincidir con la suma de capital, interés e IVA."};
    }
    totalCapital += capital;
  }
  if (principal != null && minorUnits(principal) !== totalCapital) {
    return {index:0, field:"capital", message:"La suma del capital de las cuotas debe coincidir con el capital del préstamo."};
  }
  return null;
}
