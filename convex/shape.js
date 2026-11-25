import {v} from "convex/values";

const optionalText = v.optional(v.string());
export const profile = v.object({
  name:v.string(), id_type:v.string(), id_number:v.string(),
  address:v.optional(v.object({
    street:optionalText, number:optionalText, floor:optionalText, apartment:optionalText, locality:optionalText, province:optionalText,
  })),
});
export const party = {kind:v.string(), digits:v.string(), nameKey:v.string(), profile};
export const importedParty = v.object({...party, fingerprint:v.string()});
