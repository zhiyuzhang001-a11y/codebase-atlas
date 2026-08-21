export class PrimaryWorker {
  run(value: number): number {
    return value + 1;
  }
}

export class SecondaryWorker {
  run(value: number): number {
    return value - 1;
  }
}
