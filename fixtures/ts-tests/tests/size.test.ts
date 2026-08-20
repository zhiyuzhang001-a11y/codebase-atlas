import { parseSize } from '../src/size.js';

describe('parseSize', () => {
  it('parses a number', () => {
    expect(parseSize('12')).toBe(12);
  });

  test('handles whitespace', () => {
    expect(parseSize(' 7')).toBe(7);
  });

  it('does not match a string mention', () => {
    expect('parseSize').toBe('parseSize');
  });
});
