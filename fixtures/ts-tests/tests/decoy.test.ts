import { parseSize } from '../src/decoy.js';

it('uses the same-name decoy', () => {
  expect(parseSize('word')).toBe(4);
});
