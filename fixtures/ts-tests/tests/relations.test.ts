import { describe, it } from 'vitest';
import { root } from '../src/relations.js';

function invokeRoot(): number {
  return root(2);
}

describe('root helper suite', () => {
  it('uses a resolved helper', () => {
    expect(invokeRoot()).toBe(10);
  });
});
