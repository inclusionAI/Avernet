import type { HumanInputSchema } from "../types.js";

type LooseObject = Record<string, unknown>;

type FieldSpec = {
  name: string;
  type?: string;
  regex?: string;
  enumValues?: unknown[];
  required: boolean;
};

export class HumanInputValidationError extends Error {
  readonly code: "missing_required_field" | "invalid_field";
  readonly field?: string;

  constructor(params: {
    code: "missing_required_field" | "invalid_field";
    field?: string;
    message: string;
  }) {
    super(params.message);
    this.name = "HumanInputValidationError";
    this.code = params.code;
    this.field = params.field;
  }
}

export function isHumanInputValidationError(error: unknown): error is HumanInputValidationError {
  return error instanceof HumanInputValidationError;
}

function isObject(value: unknown): value is LooseObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getRequiredFields(schema: LooseObject): Set<string> {
  const required = schema.required;
  if (!Array.isArray(required)) {
    return new Set();
  }
  return new Set(required.filter((field): field is string => typeof field === "string"));
}

function getRegex(field: LooseObject): string | undefined {
  const parse = field.parse;
  if (isObject(parse) && typeof parse.regex === "string") {
    return parse.regex;
  }
  if (typeof field.regex === "string") {
    return field.regex;
  }
  if (field.parser === "regex" && typeof field.pattern === "string") {
    return field.pattern;
  }
  if (isObject(field.parser)) {
    if (typeof field.parser.regex === "string") {
      return field.parser.regex;
    }
    if (typeof field.parser.pattern === "string") {
      return field.parser.pattern;
    }
  }
  return undefined;
}

function getPlannedFields(schema: LooseObject, requiredFields: Set<string>): FieldSpec[] {
  if (!isObject(schema.properties)) {
    return [];
  }
  return Object.entries(schema.properties)
    .filter((entry): entry is [string, LooseObject] => isObject(entry[1]))
    .map(([name, field]) => ({
      name,
      type: typeof field.type === "string" ? field.type : undefined,
      regex: getRegex(field),
      enumValues: Array.isArray(field.enum) ? field.enum : undefined,
      required: requiredFields.has(name),
    }));
}

function getLegacyFields(schema: LooseObject, requiredFields: Set<string>): FieldSpec[] {
  if (!isObject(schema.fields)) {
    return [];
  }
  return Object.entries(schema.fields)
    .filter((entry): entry is [string, LooseObject] => isObject(entry[1]))
    .map(([name, field]) => ({
      name,
      type: typeof field.type === "string" ? field.type : undefined,
      regex: getRegex(field),
      enumValues: Array.isArray(field.enum) ? field.enum : undefined,
      required: requiredFields.has(name),
    }));
}

function convertValue(field: string, type: string | undefined, value: string): unknown {
  switch (type) {
    case undefined:
    case "string":
      return value;
    case "number": {
      const numberValue = Number(value.trim());
      if (!Number.isFinite(numberValue)) {
        throw new HumanInputValidationError({
          code: "invalid_field",
          field,
          message: `human input field "${field}" must be a number`,
        });
      }
      return numberValue;
    }
    case "boolean": {
      const normalized = value.trim().toLowerCase();
      if (["true", "1", "yes", "y"].includes(normalized)) {
        return true;
      }
      if (["false", "0", "no", "n"].includes(normalized)) {
        return false;
      }
      throw new HumanInputValidationError({
        code: "invalid_field",
        field,
        message: `human input field "${field}" must be a boolean`,
      });
    }
    default:
      return value;
  }
}

export function parseHumanInput(
  schema: HumanInputSchema | LooseObject | undefined,
  text: string,
): Record<string, unknown> {
  if (!isObject(schema)) {
    return {};
  }

  const requiredFields = getRequiredFields(schema);
  const fields = getPlannedFields(schema, requiredFields);
  const activeFields = fields.length > 0 ? fields : getLegacyFields(schema, requiredFields);
  const result: Record<string, unknown> = {};

  for (const field of activeFields) {
    if (!field.regex) {
      continue;
    }
    const match = new RegExp(field.regex).exec(text);
    if (match?.[1] !== undefined) {
      const converted = convertValue(field.name, field.type, match[1]);
      if (field.enumValues && !field.enumValues.includes(converted)) {
        throw new HumanInputValidationError({
          code: "invalid_field",
          field: field.name,
          message: `human input field "${field.name}" must be one of ${field.enumValues.join(", ")}`,
        });
      }
      result[field.name] = converted;
    }
  }

  const requiredStringFieldsWithoutRegex = activeFields.filter((field) => (
    field.required && field.type === "string" && !field.regex
  ));
  if (requiredStringFieldsWithoutRegex.length === 1 && requiredFields.size === 1) {
    const field = requiredStringFieldsWithoutRegex[0];
    result[field.name] = text;
    if (field.enumValues && !field.enumValues.includes(text)) {
      throw new HumanInputValidationError({
        code: "invalid_field",
        field: field.name,
        message: `human input field "${field.name}" must be one of ${field.enumValues.join(", ")}`,
      });
    }
  }

  for (const field of requiredFields) {
    if (!(field in result)) {
      throw new HumanInputValidationError({
        code: "missing_required_field",
        field,
        message: `missing required human input field "${field}"`,
      });
    }
  }

  return result;
}
