import fs from 'node:fs';
import path from 'node:path';
import Ajv from 'ajv';
import addFormats from 'ajv-formats';

function resolveSchemaPath() {
  const candidates = [
    process.env.AXE_SCHEMA_PATH,
    '/contracts/axe-violation-payload.schema.json',
    path.resolve(process.cwd(), '../contracts/axe-violation-payload.schema.json'),
    path.resolve(process.cwd(), '../../contracts/axe-violation-payload.schema.json')
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  throw new Error('Could not locate axe-violation-payload.schema.json in known paths.');
}

const schemaPath = resolveSchemaPath();
const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf-8'));

const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);
const validate = ajv.compile(schema);

export function validateAxePayload(payload) {
  const valid = validate(payload);
  if (!valid) {
    const details = (validate.errors || []).map((e) => `${e.instancePath || '/'} ${e.message}`).join('; ');
    throw new Error(`AxeViolationPayload schema validation failed: ${details}`);
  }
}
