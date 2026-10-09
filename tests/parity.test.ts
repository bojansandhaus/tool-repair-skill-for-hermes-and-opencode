/**
 * Behavioural parity between the Python and TypeScript implementations.
 *
 * The skill ships both. Until now CI ran only the Python suite, so a divergence
 * in either direction shipped green. These cases are the ones where the two
 * implementations were measured to disagree, plus the guards around them.
 *
 * Run with: npx --yes -p typescript@5 -p tsx@4 tsx tests/parity.test.ts
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { repairFunctionArgs } from '../adapters/opencode/tool_repair.js';

const arrSchema = {
  type: 'object',
  properties: { files: { type: 'array', items: { type: 'string' } } },
} as never;

const unionSchema = {
  type: 'object',
  properties: { files: { anyOf: [{ type: 'array' }, { type: 'string' }] } },
} as never;

const oneOfSchema = {
  type: 'object',
  properties: { files: { oneOf: [{ type: 'array' }, { type: 'string' }] } },
} as never;

test('stringified array is parsed', () => {
  const [args, notes] = repairFunctionArgs('t', { files: '["a.txt"]' }, arrSchema);
  assert.deepEqual(args, { files: ['a.txt'] });
  assert.equal(notes.length, 1);
});

test('a stringified array with trailing whitespace is still parsed', () => {
  // Python wrapped the junk string as one array element here, because it tested
  // startswith/endswith without trimming. The TypeScript port already trimmed.
  const [args] = repairFunctionArgs('t', { files: '["a.txt"] ' }, arrSchema);
  assert.deepEqual(args, { files: ['a.txt'] });
});

test('a stringified array with leading whitespace is still parsed', () => {
  const [args] = repairFunctionArgs('t', { files: ' ["a.txt"]' }, arrSchema);
  assert.deepEqual(args, { files: ['a.txt'] });
});

test('an anyOf array variant is recognised', () => {
  // Only the Python side walked anyOf. A caller that passes a union schema got
  // different behaviour depending on which implementation was loaded.
  const [args] = repairFunctionArgs('t', { files: 'a.txt' }, unionSchema);
  assert.deepEqual(args, { files: ['a.txt'] });
});

test('a oneOf array variant is recognised', () => {
  const [args] = repairFunctionArgs('t', { files: 'a.txt' }, oneOfSchema);
  assert.deepEqual(args, { files: ['a.txt'] });
});

test('a bare-host autolink is unwrapped', () => {
  // This is the form the Python module documents and asserts in its own
  // self-test. The TypeScript pattern required a path segment, so it could not
  // match this at all.
  const [args, notes] = repairFunctionArgs('t', { p: '/x/[notes.md](http://notes.md)' });
  assert.equal(args.p, '/x/notes.md');
  assert.equal(notes.length, 1);
});

test('a host-plus-path autolink is unwrapped', () => {
  const [args] = repairFunctionArgs('t', { p: '[notes.md](https://example.com/notes.md)' });
  assert.equal(args.p, 'notes.md');
});

test('an https bare-host autolink is unwrapped', () => {
  const [args] = repairFunctionArgs('t', { p: '/x/[notes.md](https://notes.md)' });
  assert.equal(args.p, '/x/notes.md');
});

test('an autolink with a port is unwrapped', () => {
  const [args] = repairFunctionArgs('t', { p: '[notes.md](http://localhost:3000/notes.md)' });
  assert.equal(args.p, 'notes.md');
});

test('unicode survives the autolink rewrite', () => {
  const [args] = repairFunctionArgs('t', { p: '/tmp/[nötés.md](http://nötés.md)' });
  assert.equal(args.p, '/tmp/nötés.md');
});

test('a real markdown link is left alone', () => {
  for (const p of [
    '[click here](https://example.com/docs)',
    '[notes.md](https://example.com/docs/notes.md)',
    '[notes.md](http://notes.md?raw=1)',
  ]) {
    const [args, notes] = repairFunctionArgs('t', { p });
    assert.equal(args.p, p, p);
    assert.equal(notes.length, 0, p);
  }
});

test('two autolinks in one value are both rewritten', () => {
  const [args] = repairFunctionArgs(
    't',
    { p: '[a.md](https://x.com/a.md) and [b.md](https://x.com/b.md)' },
  );
  assert.equal(args.p, 'a.md and b.md');
});

test('a null on a required field is preserved', () => {
  const [args, notes] = repairFunctionArgs(
    't',
    { limit: null },
    { type: 'object', required: ['limit'], properties: { limit: { type: 'number' } } } as never,
  );
  assert.ok('limit' in args, 'a required null must survive for validation to report');
  assert.equal(notes.length, 0);
});

test('a null on a nullable field is preserved', () => {
  const [args] = repairFunctionArgs(
    't',
    { limit: null },
    { type: 'object', properties: { limit: { type: ['number', 'null'] } } } as never,
  );
  assert.ok('limit' in args);
});

test('a null on an optional field is removed', () => {
  const [args, notes] = repairFunctionArgs(
    't',
    { limit: null },
    { type: 'object', properties: { limit: { type: 'number' } } } as never,
  );
  assert.ok(!('limit' in args));
  assert.equal(notes.length, 1);
});

test('a bare string required field is not treated as a character set', () => {
  // Python did set("files") -> {'f','i','l','e','s'}, so a null on a field
  // named "f" would be preserved and one named "z" deleted.
  const [args] = repairFunctionArgs(
    't',
    { z: null },
    { type: 'object', required: 'files', properties: { z: { type: 'number' } } } as never,
  );
  assert.deepEqual(args, {});
});

test('a malformed schema does not throw', () => {
  for (const schema of [
    { type: 'object', properties: { files: null } },
    { type: 'object', properties: { files: { anyOf: null } } },
    { type: 'object', properties: { files: { anyOf: { type: 'array' } } } },
    { type: 'object', properties: { files: { anyOf: [null, { type: 'array' }] } } },
    { type: 'object', properties: null },
    { type: 'object', properties: { files: 'array' } },
    // Boolean schemas are valid JSON Schema, and so is a field typed only as
    // a bare scalar. The Python walker raised on each of these; it now carries
    // the same guards, so the two agree on the outcome and not just on survival.
    { type: 'object', properties: { files: true } },
    { type: 'object', properties: { files: false } },
    { type: 'object', properties: { files: 3 } },
    { type: 'object', properties: { files: { oneOf: [null] } } },
    { type: 'object', properties: { files: { anyOf: 'array' } } },
    { type: 'object', required: 'files', properties: { files: { type: 'array' } } },
    { type: 'object', required: null },
  ]) {
    assert.doesNotThrow(
      () => repairFunctionArgs('t', { files: 'a.txt' }, schema as never),
      JSON.stringify(schema),
    );
  }
});

test('a malformed or boolean schema repairs nothing', () => {
  // The Python side is asserted value-for-value against the same list in
  // tests/test_tool_repair.py::test_a_malformed_or_boolean_schema_does_not_raise.
  for (const schema of [
    { type: 'object', properties: null },
    { type: 'object', properties: { files: null } },
    { type: 'object', properties: { files: true } },
    { type: 'object', properties: { files: false } },
    { type: 'object', properties: { files: 'array' } },
    { type: 'object', properties: { files: 3 } },
    { type: 'object', properties: { files: { anyOf: { type: 'array' } } } },
    { type: 'object', properties: { files: { anyOf: 'array' } } },
  ]) {
    const [args, notes] = repairFunctionArgs('t', { files: 'a.txt' }, schema as never);
    assert.deepEqual(args, { files: 'a.txt' }, JSON.stringify(schema));
    assert.equal(notes.length, 0, JSON.stringify(schema));
  }
});

test('a union with a null entry still declares an array', () => {
  // The guard must not swallow the declaration it sits next to.
  const [args, notes] = repairFunctionArgs(
    't',
    { files: 'a.txt' },
    { type: 'object', properties: { files: { anyOf: [null, { type: 'array' }] } } } as never,
  );
  assert.deepEqual(args, { files: ['a.txt'] });
  assert.equal(notes.length, 1);
});

test('a type union that names array is recognised', () => {
  // Python compared `type == "array"` only, so a `["string","array"]` union was
  // invisible to it and the bare-string repair silently did not fire. Both
  // ports now accept the union, which is what the null walker already did for
  // a union naming "null".
  for (const schema of [
    { type: 'object', properties: { files: { type: ['string', 'array'] } } },
    { type: 'object', properties: { files: { type: ['array', 'string'] } } },
  ]) {
    const [wrapped] = repairFunctionArgs('t', { files: 'a.txt' }, schema as never);
    assert.deepEqual(wrapped, { files: ['a.txt'] }, JSON.stringify(schema));
    const [emptied] = repairFunctionArgs('t', { files: {} }, schema as never);
    assert.deepEqual(emptied, { files: [] }, JSON.stringify(schema));
  }
});

test('arguments without a schema are left alone', () => {
  // This is what the OpenCode adapter does today. It is why the two
  // schema-aware repairs cannot fire in that adapter.
  for (const args of [
    { files: {} },
    { files: 'foo.txt' },
    { files: ' [not json] ' },
    { files: { a: 1 } },
    { files: ['a.txt'] },
  ]) {
    const [out, notes] = repairFunctionArgs('t', structuredClone(args));
    assert.deepEqual(out, args, JSON.stringify(args));
    assert.equal(notes.length, 0, JSON.stringify(args));
  }
});

test('a schema with no properties leaves the array repairs dormant', () => {
  // A schema that declares no array fields is the same as no schema for these
  // two repairs, which is what SKILL.md and README.md both promise.
  for (const schema of [{ type: 'object' }, {}, { required: ['files'] }]) {
    const [out, notes] = repairFunctionArgs('t', { files: 'a.txt' }, schema as never);
    assert.deepEqual(out, { files: 'a.txt' }, JSON.stringify(schema));
    assert.equal(notes.length, 0, JSON.stringify(schema));
  }
});

test('a bracket-shaped string is never wrapped as an array element', () => {
  // `["[not json]"]` is a valid array holding one garbage element, which hides
  // the real type error from the validator. The Python port wrapped these too;
  // both now skip them, so the two still agree.
  for (const junk of [
    ' [not json] ',
    '[not json]',
    '[1, 2] and [3, 4]',
    '[a.md] and [b.md]',
  ]) {
    const [out, notes] = repairFunctionArgs(
      't',
      { files: junk },
      { type: 'object', properties: { files: { type: 'array' } } } as never,
    );
    assert.deepEqual(out, { files: junk }, junk);
    assert.equal(notes.length, 0, junk);
  }
});

test('an ordinary bare string is still wrapped', () => {
  const [out, notes] = repairFunctionArgs(
    't',
    { files: ' foo.txt ' },
    { type: 'object', properties: { files: { type: 'array' } } } as never,
  );
  assert.deepEqual(out, { files: [' foo.txt '] });
  assert.equal(notes.length, 1);
});

test('repair is idempotent', () => {
  const once = repairFunctionArgs(
    't',
    { files: '["a.txt"]', p: '/x/[n.md](http://n.md)' },
    arrSchema,
  )[0];
  const twice = repairFunctionArgs('t', once, arrSchema)[0];
  assert.deepEqual(twice, once);
});

test('a valid call produces no notes and no mutation', () => {
  const input = { files: ['a.txt'], limit: 5 };
  const snapshot = JSON.stringify(input);
  const [args, notes] = repairFunctionArgs('t', input, arrSchema);
  assert.deepEqual(notes, []);
  assert.equal(JSON.stringify(input), snapshot);
  assert.deepEqual(args, input);
});

test('nested argument objects are left alone by both implementations', () => {
  // Documented as top-level-only rather than a divergence, pinned so a future
  // change to one side does not silently diverge.
  const [args, notes] = repairFunctionArgs('t', { outer: { files: {} } }, arrSchema);
  assert.deepEqual(args, { outer: { files: {} } });
  assert.equal(notes.length, 0);
});