export const addressFields = {street:"Calle", number:"Altura", floor:"Piso", apartment:"Departamento", locality:"Localidad", province:"Provincia"};

export function addressLine({street, number, floor, apartment, locality, province}) {
  return [[street, number].filter(Boolean).join(" "), floor && "piso " + floor, apartment && "depto. " + apartment,
    locality, province].filter(Boolean).join(", ");
}
