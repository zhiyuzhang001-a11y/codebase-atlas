import { PrimaryWorker, SecondaryWorker } from '../src/members.js';

it('runs the primary worker', () => {
  expect(new PrimaryWorker().run(1)).toBe(2);
});

it('runs the secondary worker', () => {
  expect(new SecondaryWorker().run(1)).toBe(0);
});
