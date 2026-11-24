export const taxKind = "CUIT/CUIL";
const bindings = new WeakMap();
const numericText = /^[0-9.\s-]*$/;
const separator = /[.\s-]/;

export function identificationGroup(kind) {
  return kind === "CUIT" || kind === "CUIL" ? taxKind : kind;
}

export function documentDigits(text) {
  return text.replace(/[.\s-]/g, "");
}

export function displayNumber(kind, text) {
  if (!numericText.test(text)) return text;
  const digits = documentDigits(text);
  if (identificationGroup(kind) === taxKind) {
    return [digits.slice(0, 2), digits.slice(2, 10), digits.slice(10)].filter(Boolean).join("-");
  }
  if (kind === "DNI" && digits.length <= 8) return digits.replace(/\B(?=(?:[0-9]{3})+$)/g, ".");
  return digits;
}

const digitsOnly = text => text.replace(/[^0-9]/g, "");
const digitLimit = kind => kind === "DNI" ? 8 : 11;

export function boundedInsertion(previous, start, end, inserted, limit) {
  const left = digitsOnly(previous.slice(0, start));
  const right = digitsOnly(previous.slice(end));
  const accepted = digitsOnly(inserted).slice(0, Math.max(0, limit - left.length - right.length));
  if (!accepted && inserted) {
    return {digits:digitsOnly(previous), start:left.length, end:digitsOnly(previous.slice(0, end)).length};
  }
  const caret = left.length + accepted.length;
  return {digits:left + accepted + right, start:caret, end:caret};
}

function caretAfterDigits(value, count) {
  if (!count) return 0;
  for (let i = 0; i < value.length; i++) {
    if (/[0-9]/.test(value[i]) && --count === 0) return i + 1;
  }
  return value.length;
}

function inferEdit(previous, raw, caret) {
  // Some replacement and autofill events skip beforeinput. The text after the caret is still untouched.
  const suffix = raw.slice(caret);
  if (!previous.endsWith(suffix)) return null;
  const end = previous.length - suffix.length;
  let start = 0;
  while (start < end && start < caret && previous[start] === raw[start]) start++;
  return {value:previous, start, end};
}

export function bindIdentification(type, number, fallback, report) {
  const state = {type, fallback, knownKind:null, knownDigits:""};
  bindings.set(number, state);
  number.inputMode = "numeric";
  let before = null;
  let lastValue = "";
  function synchronize() {
    const kind = type.value || fallback;
    number.placeholder = kind === "DNI" ? "12.345.678" : "00-00000000-0";
    const raw = number.value;
    // An incompatible stored number or previous type must never silently become another identity.
    number.value = numericText.test(raw) && documentDigits(raw).length <= digitLimit(kind) ? displayNumber(kind, raw) : "";
    lastValue = number.value;
    before = null;
  }
  state.synchronize = synchronize;
  // The native editor is also the event origin inside a Material text field.
  number.addEventListener("beforeinput", event => {
    const input = event.composedPath()[0];
    before = null;
    if (!(input instanceof HTMLInputElement)) return;
    if (event.inputType?.startsWith("insert")) {
      before = {value:input.value, start:input.selectionStart, end:input.selectionEnd};
      return;
    }
    if (input.selectionStart !== input.selectionEnd || !numericText.test(input.value)) return;
    const pos = input.selectionStart;
    if (event.inputType === "deleteContentBackward" && pos > 0 && separator.test(input.value[pos - 1])) {
      input.setSelectionRange(Math.max(0, pos - 2), pos);
    } else if (event.inputType === "deleteContentForward" && pos < input.value.length && separator.test(input.value[pos])) {
      input.setSelectionRange(pos, Math.min(input.value.length, pos + 2));
    }
  }, true);
  number.addEventListener("input", event => {
    const input = event.composedPath()[0];
    if (!(input instanceof HTMLInputElement)) return;
    const raw = input.value;
    const kind = type.value || fallback;
    const limit = digitLimit(kind);
    const edit = before || (event.inputType?.startsWith("delete") ? null : inferEdit(lastValue, raw, input.selectionStart));
    before = null;
    let result;
    if (edit) {
      const prefix = edit.value.slice(0, edit.start);
      const suffix = edit.value.slice(edit.end);
      if (raw.length >= prefix.length + suffix.length && raw.startsWith(prefix) && raw.endsWith(suffix)) {
        result = boundedInsertion(edit.value, edit.start, edit.end, raw.slice(prefix.length, raw.length - suffix.length), limit);
      }
    }
    // Autofill and whole-field replacements need not produce beforeinput.
    if (!result) {
      result = {digits:digitsOnly(raw).slice(0, limit), start:digitsOnly(raw.slice(0, input.selectionStart)).length,
        end:digitsOnly(raw.slice(0, input.selectionEnd)).length};
    }
    const formatted = displayNumber(kind, result.digits);
    lastValue = formatted;
    if (raw === formatted) return;
    input.value = formatted;
    number.value = formatted;
    input.setSelectionRange(caretAfterDigits(formatted, result.start), caretAfterDigits(formatted, result.end));
  }, true);
  type.addEventListener("change", () => {
    synchronize();
    if (!number.disabled) report(number, identificationProblem(number));
  });
  number.closest("form").addEventListener("reset", () => {
    state.knownKind = null;
    report(number, "");
    queueMicrotask(synchronize);
  });
  synchronize();
}

export function writeIdentification(number, party) {
  const state = bindings.get(number);
  const kind = party.id_type;
  state.type.value = identificationGroup(kind) || state.fallback;
  number.value = typeof party.id_number === "string" ? party.id_number : "";
  state.knownKind = kind === "CUIT" || kind === "CUIL" ? kind : null;
  state.knownDigits = documentDigits(number.value);
  state.synchronize();
}

export function readIdentification(number) {
  const state = bindings.get(number);
  const group = state.type.value || state.fallback;
  const unchanged = group === taxKind && state.knownKind && documentDigits(number.value) === state.knownDigits;
  return {id_type:unchanged ? state.knownKind : group, id_number:number.value};
}

export function identificationProblem(number) {
  const state = bindings.get(number);
  if (!state || !number.value.trim()) return "";
  const kind = state.type.value || state.fallback;
  const digits = documentDigits(number.value);
  if (kind === "DNI") return /^[0-9]{1,8}$/.test(digits) ? "" : "El DNI debe tener entre 1 y 8 dígitos.";
  return /^[0-9]{11}$/.test(digits) ? "" : "El CUIT/CUIL debe tener 11 dígitos.";
}
