export function target(value: number): number {
  return value + 1;
}

export function targetAsync(value: number): number {
  return value - 1;
}

export const arrowCaller = (value: number): number => target(value);

export const sameNameDecoy = (value: number): number => targetAsync(value);

export function first(value: number): number {
  return value * 2;
}

export function second(value: number): number {
  return value * 3;
}

export function root(value: number): number {
  return first(value) + second(value);
}

export function overloaded(value: number): number;
export function overloaded(value: string): number;
export function overloaded(value: number | string): number {
  return first(Number(value));
}
