import test from 'node:test';
import assert from 'node:assert/strict';
import { draftField } from '../static/draft.mjs';

test('unknown extracted money and currency never become default values', () => {
  for (const name of ['tax', 'discount', 'currency', 'declared_total']) {
    assert.equal(draftField({[name]: null}, name), '');
    assert.equal(draftField({}, name), '');
  }
});
test('explicit zeros and known values survive adoption', () => {
  assert.equal(draftField({tax: '0.00'}, 'tax'), '0.00');
  assert.equal(draftField({tax: 0}, 'tax'), 0);
  assert.equal(draftField({currency: 'EUR'}, 'currency'), 'EUR');
});
test('defaults are restricted to new manual forms', () => {
  assert.equal(draftField(null, 'tax'), '0');
  assert.equal(draftField(undefined, 'currency'), 'PKR');
});
