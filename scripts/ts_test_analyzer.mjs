#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
let ts;
for (const candidate of [
  path.join(scriptDirectory, 'node_modules/typescript/lib/typescript.js'),
  path.join(scriptDirectory, '../vendor/typescript/lib/typescript.js'),
]) {
  if (fs.existsSync(candidate)) {
    ts = require(candidate);
    break;
  }
}
if (!ts) {
  throw new Error('the pinned TypeScript runtime is missing; reinstall Codebase Atlas');
}

function parseArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith('--') || value === undefined) {
      throw new Error(`invalid argument sequence near ${key ?? '<end>'}`);
    }
    result[key.slice(2)] = value;
  }
  return result;
}

function normalizedRelativePath(root, filename) {
  return path.relative(root, filename).split(path.sep).join('/');
}

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function canonicalSymbol(checker, symbol) {
  if (!symbol) return undefined;
  if ((symbol.flags & ts.SymbolFlags.Alias) !== 0) {
    return checker.getAliasedSymbol(symbol);
  }
  return symbol;
}

function declarationName(node) {
  if (
    ts.isFunctionDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isVariableDeclaration(node)
  ) {
    return node.name && ts.isIdentifier(node.name) ? node.name : undefined;
  }
  return undefined;
}

function testApiName(expression) {
  if (ts.isIdentifier(expression)) {
    return expression.text === 'it' || expression.text === 'test' ? expression.text : undefined;
  }
  if (ts.isPropertyAccessExpression(expression)) {
    return testApiName(expression.expression);
  }
  if (ts.isCallExpression(expression)) {
    return testApiName(expression.expression);
  }
  return undefined;
}

function suiteApiName(expression) {
  if (ts.isIdentifier(expression)) {
    return expression.text === 'describe' ? expression.text : undefined;
  }
  if (ts.isPropertyAccessExpression(expression)) return suiteApiName(expression.expression);
  if (ts.isCallExpression(expression)) return suiteApiName(expression.expression);
  return undefined;
}

function callbackArgument(call) {
  return [...call.arguments]
    .reverse()
    .find((argument) => ts.isArrowFunction(argument) || ts.isFunctionExpression(argument));
}

function testTitle(call, line) {
  const first = call.arguments[0];
  if (first && (ts.isStringLiteral(first) || ts.isNoSubstitutionTemplateLiteral(first))) {
    return first.text;
  }
  return `<dynamic-test@${line}>`;
}

function declarationKey(node) {
  return `${path.resolve(node.getSourceFile().fileName)}:${node.getStart(node.getSourceFile())}`;
}

function callbackCallsTarget(callback, checker, targetSymbols, targetDeclarationKeys) {
  let matched = false;
  function visit(node) {
    if (matched) return;
    if (ts.isCallExpression(node)) {
      const symbol = canonicalSymbol(checker, checker.getSymbolAtLocation(node.expression));
      const declarationMatch = symbol?.declarations?.some(
        declaration => targetDeclarationKeys.has(declarationKey(declaration)),
      );
      if (symbol && (targetSymbols.has(symbol) || declarationMatch)) {
        matched = true;
        return;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(callback.body);
  return matched;
}

function symbolMatchesTarget(symbol, targetSymbols, targetDeclarationKeys) {
  return Boolean(symbol && (
    targetSymbols.has(symbol) || symbol.declarations?.some(
      declaration => targetDeclarationKeys.has(declarationKey(declaration)),
    )
  ));
}

function callableDeclaration(node) {
  for (let current = node; current; current = current.parent) {
    if (
      ts.isFunctionDeclaration(current) || ts.isMethodDeclaration(current) ||
      ts.isGetAccessorDeclaration(current) || ts.isSetAccessorDeclaration(current)
    ) {
      return current;
    }
    if (ts.isArrowFunction(current) || ts.isFunctionExpression(current)) {
      if (ts.isVariableDeclaration(current.parent)) return current.parent;
      if (ts.isPropertyDeclaration(current.parent) || ts.isPropertyAssignment(current.parent)) {
        return current.parent;
      }
      return current;
    }
  }
  return undefined;
}

function callableName(node) {
  const name = node?.name;
  if (name && (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name))) {
    return name.text;
  }
  return '<anonymous>';
}

function exactDeclarationNode(repository, declaration, provider) {
  const sourceFile = declaration.getSourceFile();
  const relativePath = normalizedRelativePath(repository, sourceFile.fileName);
  const start = sourceFile.getLineAndCharacterOfPosition(declaration.getStart(sourceFile));
  const end = sourceFile.getLineAndCharacterOfPosition(declaration.end);
  const name = callableName(declaration);
  const evidence = declaration.getText(sourceFile);
  return {
    id: `ts:callable:${relativePath}:${start.line + 1}:${sha256(name).slice(0, 12)}`,
    kind: ts.isMethodDeclaration(declaration) ? 'method' : 'function',
    name,
    location: {
      path: relativePath,
      start_line: start.line + 1,
      end_line: end.line + 1,
      start_column: start.character + 1,
      end_column: end.character + 1,
    },
    provider,
    confidence: 1.0,
    evidence_hash: sha256(evidence),
    attributes: { strategy: 'typescript_compiler_symbol_identity' },
  };
}

function directTargetCall(node, checker, targetSymbols, targetDeclarationKeys) {
  if (!ts.isCallExpression(node)) return false;
  const symbol = canonicalSymbol(checker, checker.getSymbolAtLocation(node.expression));
  return symbolMatchesTarget(symbol, targetSymbols, targetDeclarationKeys);
}

function reachableTargetEvidence(root, checker, targetSymbols, targetDeclarationKeys) {
  const visitedDeclarations = new Set();
  let evidence;
  function visit(node) {
    if (evidence) return;
    if (directTargetCall(node, checker, targetSymbols, targetDeclarationKeys)) {
      evidence = node;
      return;
    }
    if (ts.isCallExpression(node)) {
      const symbol = canonicalSymbol(checker, checker.getSymbolAtLocation(node.expression));
      for (const declaration of symbol?.declarations ?? []) {
        const key = declarationKey(declaration);
        if (visitedDeclarations.has(key)) continue;
        visitedDeclarations.add(key);
        const callable = callableDeclaration(declaration);
        if (callable) visit(callable);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(root.body ?? root);
  return evidence;
}

function fileDiverseOrder(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.node.location.path;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const paths = [...groups.keys()].sort((left, right) => left.localeCompare(right));
  const ordered = [];
  for (let index = 0; ; index += 1) {
    let added = false;
    for (const filePath of paths) {
      const item = groups.get(filePath)[index];
      if (item) {
        ordered.push(item);
        added = true;
      }
    }
    if (!added) return ordered;
  }
}

function hasEnclosingSuite(call) {
  for (let parent = call.parent; parent; parent = parent.parent) {
    if (ts.isCallExpression(parent) && suiteApiName(parent.expression)) return true;
  }
  return false;
}

function loadProgram(repository, selectedConfig = '', targetPath = '') {
  const configPath = selectedConfig
    ? path.resolve(repository, selectedConfig)
    : ts.findConfigFile(repository, fs.existsSync, 'tsconfig.json');
  if (!configPath) throw new Error(`tsconfig.json not found under ${repository}`);
  const loaded = ts.readConfigFile(configPath, ts.sys.readFile);
  if (loaded.error) {
    throw new Error(ts.flattenDiagnosticMessageText(loaded.error.messageText, '\n'));
  }
  const config = ts.parseJsonConfigFileContent(loaded.config, ts.sys, path.dirname(configPath));
  // Production tsconfigs commonly exclude *.spec.ts even though those files are
  // exactly the evidence Atlas must inspect. Add test roots without changing the
  // repository configuration.
  const projectRoot = path.dirname(configPath);
  const testFiles = ts.sys.readDirectory(
    projectRoot,
    ['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs'],
    ['**/node_modules/**', '**/dist/**', '**/build/**', '**/coverage/**'],
    ['**/test/**', '**/tests/**', '**/__tests__/**', '**/*.test.*', '**/*.spec.*'],
  );
  let productionRoots = config.fileNames;
  if (targetPath) {
    const targetFile = path.resolve(repository, targetPath);
    const configuredFiles = new Set(config.fileNames.map((filename) => path.resolve(filename)));
    if (!configuredFiles.has(targetFile)) {
      throw new Error(`${targetPath} is outside the selected TypeScript project ${configPath}`);
    }
    // Imports are loaded transitively, so a scoped query only needs its intended
    // declaration plus the selected project's tests as roots. This prevents an
    // unrelated monorepo package from consuming the entire compiler heap.
    productionRoots = [targetFile];
  }
  const rootNames = [...new Set([...productionRoots, ...testFiles])];
  return ts.createProgram({ rootNames, options: config.options });
}

function main() {
  const args = parseArguments(process.argv.slice(2));
  const repository = path.resolve(args.repo ?? '');
  const query = args.symbol ?? '';
  const targetPath = args['target-path'] ?? '';
  const targetOwner = args['target-owner'] ?? '';
  const queryType = args['query-type'] ?? 'related_tests';
  if (!['related_tests', 'references', 'callers', 'callees'].includes(queryType)) {
    throw new Error(`unsupported query type: ${queryType}`);
  }
  if (!query || !fs.statSync(repository).isDirectory()) {
    throw new Error('--repo must be a directory and --symbol is required');
  }

  const program = loadProgram(repository, args.tsconfig ?? '', targetPath);
  const checker = program.getTypeChecker();
  const targetSymbols = new Set();
  const targetDeclarationKeys = new Set();
  const targetDeclarations = [];

  for (const sourceFile of program.getSourceFiles()) {
    if (sourceFile.isDeclarationFile || sourceFile.fileName.includes(`${path.sep}node_modules${path.sep}`)) continue;
    const relativePath = normalizedRelativePath(repository, sourceFile.fileName);
    if (targetPath && relativePath !== targetPath) continue;
    function findDeclarations(node) {
      const name = declarationName(node);
      let owner = '';
      for (let parent = node.parent; parent; parent = parent.parent) {
        if ((ts.isClassDeclaration(parent) || ts.isClassExpression(parent) || ts.isInterfaceDeclaration(parent)) && parent.name) {
          owner = parent.name.text;
          break;
        }
      }
      if (name?.text === query && (!targetOwner || owner === targetOwner)) {
        const symbol = canonicalSymbol(checker, checker.getSymbolAtLocation(name));
        if (symbol) {
          targetDeclarationKeys.add(declarationKey(node));
          const existing = targetDeclarations.findIndex(item => item.symbol === symbol);
          if (existing < 0) {
            targetSymbols.add(symbol);
            targetDeclarations.push({ sourceFile, node, symbol });
          } else if (!targetDeclarations[existing].node.body && node.body) {
            targetDeclarations[existing] = { sourceFile, node, symbol };
          }
        }
      }
      ts.forEachChild(node, findDeclarations);
    }
    findDeclarations(sourceFile);
  }

  if (targetDeclarations.length !== 1) {
    throw new Error(
      `expected one declaration for ${targetOwner ? `${targetOwner}.` : ''}${query}${targetPath ? ` in ${targetPath}` : ''}, found ${targetDeclarations.length}`,
    );
  }

  const target = targetDeclarations[0];
  const targetRelativePath = normalizedRelativePath(repository, target.sourceFile.fileName);
  const targetStart = target.sourceFile.getLineAndCharacterOfPosition(target.node.getStart(target.sourceFile));
  const targetId = `ts:declaration:${targetRelativePath}:${targetStart.line + 1}:${query}`;
  const results = [];

  if (queryType === 'references') {
    for (const sourceFile of program.getSourceFiles()) {
      if (sourceFile.isDeclarationFile || sourceFile.fileName.includes(`${path.sep}node_modules${path.sep}`)) continue;
      const relativePath = normalizedRelativePath(repository, sourceFile.fileName);
      function findReferences(node) {
        if (ts.isIdentifier(node) && node.text === query && node !== target.node.name) {
          const symbol = canonicalSymbol(checker, checker.getSymbolAtLocation(node));
          const declarationMatch = symbol?.declarations?.some(
            declaration => targetDeclarationKeys.has(declarationKey(declaration)),
          );
          if (symbol && (targetSymbols.has(symbol) || declarationMatch)) {
            const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
            const end = sourceFile.getLineAndCharacterOfPosition(node.end);
            const lineStart = sourceFile.getPositionOfLineAndCharacter(start.line, 0);
            const evidence = sourceFile.text.slice(lineStart, sourceFile.getLineEndOfPosition(node.end));
            results.push({
              id: `ts:reference:${targetRelativePath}:${targetStart.line + 1}:${relativePath}:${start.line + 1}:${start.character + 1}`,
              kind: 'reference',
              name: query,
              location: {
                path: relativePath,
                start_line: start.line + 1,
                end_line: end.line + 1,
                start_column: start.character + 1,
                end_column: end.character + 1,
              },
              provider: 'atlas-ts-references',
              confidence: 1.0,
              evidence_hash: sha256(evidence),
              attributes: { provider_id: targetId },
            });
          }
        }
        ts.forEachChild(node, findReferences);
      }
      findReferences(sourceFile);
    }
    results.sort((left, right) =>
      left.location.path.localeCompare(right.location.path) ||
      left.location.start_line - right.location.start_line ||
      (left.location.start_column ?? 0) - (right.location.start_column ?? 0),
    );
    process.stdout.write(`${JSON.stringify({ schema_version: 1, query_type: queryType, target_id: targetId, results }, null, 2)}\n`);
    return;
  }

  if (queryType === 'callers') {
    const seen = new Set();
    for (const sourceFile of program.getSourceFiles()) {
      if (sourceFile.isDeclarationFile || sourceFile.fileName.includes(`${path.sep}node_modules${path.sep}`)) continue;
      function findCallers(node) {
        if (directTargetCall(node, checker, targetSymbols, targetDeclarationKeys)) {
          const declaration = callableDeclaration(node.parent);
          if (declaration && declaration !== target.node) {
            const caller = exactDeclarationNode(repository, declaration, 'atlas-ts-relations');
            if (!seen.has(caller.id)) {
              seen.add(caller.id);
              results.push({
                node: caller,
                edge: {
                  source_id: caller.id,
                  target_id: targetId,
                  relation: 'calls',
                  provider: 'atlas-ts-relations',
                  confidence: 1.0,
                  evidence_hash: sha256(node.getText(sourceFile)),
                  resolution: 'exact',
                  attributes: { strategy: 'typescript_compiler_symbol_identity' },
                },
              });
            }
          }
        }
        ts.forEachChild(node, findCallers);
      }
      findCallers(sourceFile);
    }
    results.sort((left, right) =>
      left.node.location.path.localeCompare(right.node.location.path) ||
      left.node.location.start_line - right.node.location.start_line,
    );
    process.stdout.write(`${JSON.stringify({ schema_version: 1, query_type: queryType, target_id: targetId, results }, null, 2)}\n`);
    return;
  }

  if (queryType === 'callees') {
    const seen = new Set();
    function findCallees(node) {
      if (ts.isCallExpression(node)) {
        const symbol = canonicalSymbol(checker, checker.getSymbolAtLocation(node.expression));
        for (const declaration of symbol?.declarations ?? []) {
          const callable = callableDeclaration(declaration);
          if (!callable || callable === target.node) continue;
          const callee = exactDeclarationNode(repository, callable, 'atlas-ts-relations');
          if (seen.has(callee.id)) continue;
          seen.add(callee.id);
          results.push({
            node: callee,
            edge: {
              source_id: targetId,
              target_id: callee.id,
              relation: 'calls',
              provider: 'atlas-ts-relations',
              confidence: 1.0,
              evidence_hash: sha256(node.getText(target.sourceFile)),
              resolution: 'exact',
              attributes: { strategy: 'typescript_compiler_symbol_identity' },
            },
          });
          break;
        }
      }
      ts.forEachChild(node, findCallees);
    }
    findCallees(target.node);
    results.sort((left, right) =>
      left.node.location.path.localeCompare(right.node.location.path) ||
      left.node.location.start_line - right.node.location.start_line,
    );
    process.stdout.write(`${JSON.stringify({ schema_version: 1, query_type: queryType, target_id: targetId, results }, null, 2)}\n`);
    return;
  }

  for (const sourceFile of program.getSourceFiles()) {
    if (sourceFile.isDeclarationFile || sourceFile.fileName.includes(`${path.sep}node_modules${path.sep}`)) continue;
    const relativePath = normalizedRelativePath(repository, sourceFile.fileName);
    if (!/(^|\/)(tests?|__tests__)(\/|$)|\.(test|spec)\.[cm]?[jt]sx?$/.test(relativePath)) continue;

    function findTests(node) {
      if (ts.isCallExpression(node) && testApiName(node.expression)) {
        const callback = callbackArgument(node);
        if (callback && callbackCallsTarget(callback, checker, targetSymbols, targetDeclarationKeys)) {
          const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
          const end = sourceFile.getLineAndCharacterOfPosition(node.end);
          const line = start.line + 1;
          const title = testTitle(node, line);
          const evidence = node.getText(sourceFile);
          const evidenceHash = sha256(evidence);
          const nodeId = `ts:test:${relativePath}:${line}:${sha256(title).slice(0, 12)}`;
          results.push({
            node: {
              id: nodeId,
              kind: 'test',
              name: title,
              location: {
                path: relativePath,
                start_line: line,
                end_line: end.line + 1,
                start_column: start.character + 1,
                end_column: end.character + 1,
              },
              provider: 'atlas-ts-tests',
              confidence: 1.0,
              evidence_hash: evidenceHash,
              attributes: { framework_api: testApiName(node.expression) },
            },
            edge: {
              source_id: nodeId,
              target_id: targetId,
              relation: 'calls',
              provider: 'atlas-ts-tests',
              confidence: 1.0,
              evidence_hash: evidenceHash,
              resolution: 'exact',
              attributes: { direction: 'downstream', depth: 1 },
            },
          });
        }
      }
      if (
        ts.isCallExpression(node) && suiteApiName(node.expression) &&
        !hasEnclosingSuite(node)
      ) {
        const callback = callbackArgument(node);
        const targetCall = callback && reachableTargetEvidence(
          callback, checker, targetSymbols, targetDeclarationKeys,
        );
        const targetCallIsExternalEvidence = targetCall && (
          targetCall.getSourceFile() !== sourceFile ||
          targetCall.getStart(sourceFile) < callback.getStart(sourceFile) ||
          targetCall.end > callback.end
        );
        if (targetCall && targetCallIsExternalEvidence) {
          const suiteStart = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
          const suiteEnd = sourceFile.getLineAndCharacterOfPosition(node.end);
          const evidenceFile = targetCall.getSourceFile();
          const evidenceStart = evidenceFile === sourceFile
            ? sourceFile.getLineAndCharacterOfPosition(targetCall.getStart(sourceFile))
            : suiteStart;
          const title = testTitle(node, suiteStart.line + 1);
          const nodeId = `ts:test-suite:${relativePath}:${suiteStart.line + 1}:${sha256(title).slice(0, 12)}`;
          const evidenceHash = sha256(targetCall.getText(evidenceFile));
          results.push({
            node: {
              id: nodeId,
              kind: 'test_suite',
              name: title,
              location: {
                path: relativePath,
                start_line: Math.min(suiteStart.line, evidenceStart.line) + 1,
                end_line: suiteEnd.line + 1,
                start_column: 1,
                end_column: suiteEnd.character + 1,
              },
              provider: 'atlas-ts-tests',
              confidence: 1.0,
              evidence_hash: evidenceHash,
              attributes: {
                framework_api: suiteApiName(node.expression),
                strategy: 'enclosing_suite_via_resolved_helper_call',
              },
            },
            edge: {
              source_id: nodeId,
              target_id: targetId,
              relation: 'calls',
              provider: 'atlas-ts-tests',
              confidence: 1.0,
              evidence_hash: evidenceHash,
              resolution: 'exact',
              attributes: { direction: 'downstream', depth: 1 },
            },
          });
        }
      }
      ts.forEachChild(node, findTests);
    }
    findTests(sourceFile);
  }

  results.sort((left, right) =>
    left.node.location.path.localeCompare(right.node.location.path) ||
    left.node.location.start_line - right.node.location.start_line,
  );
  const orderedResults = fileDiverseOrder(results);
  process.stdout.write(`${JSON.stringify({ schema_version: 1, query_type: queryType, target_id: targetId, results: orderedResults }, null, 2)}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}
