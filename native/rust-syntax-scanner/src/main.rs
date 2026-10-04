use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fmt::Write as _;
use std::fs;
use std::path::{Component, PathBuf};
use tree_sitter::{Node, Parser, Point};

const ENGINE: &str =
    "tree-sitter-rust 0.24.2 via tree-sitter 0.27.0; syntax validation via syn 3.0.6";
const MAX_SOURCE_FILES: usize = 100_000;
const MAX_SOURCE_FILE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_TOTAL_SOURCE_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Deserialize)]
struct Scope {
    schema_version: u32,
    source_paths: Vec<String>,
}

#[derive(Serialize)]
struct Position {
    line: usize,
    column: usize,
}

#[derive(Serialize)]
struct Range {
    start: Position,
    end: Position,
}

#[derive(Serialize)]
struct Fact {
    path: String,
    kind: String,
    name: String,
    owner: Option<String>,
    #[serde(flatten)]
    range: Range,
}

#[derive(Serialize)]
struct FileRecord {
    path: String,
    has_parse_error: bool,
}

#[derive(Serialize)]
struct Output {
    schema_version: u32,
    provider: &'static str,
    provider_version: &'static str,
    engine: &'static str,
    files: Vec<FileRecord>,
    facts: Vec<Fact>,
    candidate_paths: Vec<String>,
    candidate_names: Vec<String>,
    candidate_owners: Vec<String>,
    identifier_candidate_count: usize,
    identifier_candidates_hex: String,
    error_files: Vec<String>,
}

fn push_candidate_word(
    output: &mut String,
    value: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    let value = u32::try_from(value).map_err(|_| "Rust candidate value exceeds u32")?;
    write!(output, "{value:08x}")?;
    Ok(())
}

fn position(point: Point) -> Position {
    Position {
        line: point.row + 1,
        column: point.column + 1,
    }
}

fn range(node: Node<'_>) -> Range {
    Range {
        start: position(node.start_position()),
        end: position(node.end_position()),
    }
}

fn text<'a>(source: &'a [u8], node: Node<'_>) -> &'a str {
    std::str::from_utf8(&source[node.byte_range()]).unwrap_or("")
}

fn safe_relative(value: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let path = PathBuf::from(value);
    if value.is_empty()
        || value.contains('\\')
        || path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(format!("unsafe Rust source path: {value}").into());
    }
    Ok(path)
}

fn is_definition(kind: &str) -> bool {
    matches!(
        kind,
        "const_item"
            | "enum_item"
            | "function_item"
            | "function_signature_item"
            | "macro_definition"
            | "mod_item"
            | "static_item"
            | "struct_item"
            | "trait_item"
            | "type_item"
            | "union_item"
    )
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() == 2 && args[1] == "--version" {
        println!(
            "{} {} ({})",
            env!("CARGO_PKG_NAME"),
            env!("CARGO_PKG_VERSION"),
            ENGINE
        );
        return Ok(());
    }
    if args.len() != 4 {
        return Err("usage: atlas-rust-syntax <repository> <scope.json> <output.json>".into());
    }
    let repository = fs::canonicalize(&args[1])?;
    let scope: Scope = serde_json::from_slice(&fs::read(&args[2])?)?;
    if scope.schema_version != 1 {
        return Err("unsupported Rust scope schema".into());
    }
    if scope.source_paths.len() > MAX_SOURCE_FILES {
        return Err("Rust source scope exceeds the file-count limit".into());
    }
    let output_path = PathBuf::from(&args[3]);
    let mut paths = Vec::new();
    let mut total_source_bytes = 0_u64;
    for raw in scope.source_paths {
        let relative = safe_relative(&raw)?;
        let canonical = fs::canonicalize(repository.join(&relative))?;
        if !canonical.starts_with(&repository)
            || canonical.extension().is_none_or(|value| value != "rs")
        {
            return Err(format!("Rust source escapes scope: {raw}").into());
        }
        let source_bytes = fs::metadata(&canonical)?.len();
        if source_bytes > MAX_SOURCE_FILE_BYTES {
            return Err(format!("Rust source exceeds the per-file limit: {raw}").into());
        }
        total_source_bytes = total_source_bytes
            .checked_add(source_bytes)
            .ok_or("Rust source byte count overflowed")?;
        if total_source_bytes > MAX_TOTAL_SOURCE_BYTES {
            return Err("Rust source scope exceeds the byte limit".into());
        }
        paths.push((raw, canonical));
    }
    paths.sort_by(|left, right| left.0.cmp(&right.0));
    paths.dedup_by(|left, right| left.0 == right.0);

    let mut parser = Parser::new();
    parser.set_language(&tree_sitter_rust::LANGUAGE.into())?;
    let mut output = Output {
        schema_version: 2,
        provider: "rust-native-syntax",
        provider_version: env!("CARGO_PKG_VERSION"),
        engine: ENGINE,
        files: Vec::new(),
        facts: Vec::new(),
        candidate_paths: paths.iter().map(|(relative, _)| relative.clone()).collect(),
        candidate_names: Vec::new(),
        candidate_owners: Vec::new(),
        identifier_candidate_count: 0,
        identifier_candidates_hex: String::new(),
        error_files: Vec::new(),
    };

    for (relative, path) in &paths {
        let source = fs::read(&path)?;
        let tree = parser
            .parse(&source, None)
            .ok_or("parser returned no tree")?;
        // Tree-sitter intentionally recovers and can lag newly accepted Rust
        // syntax.  A file is rejected only when the independent, fixed-version
        // syn parser also rejects it; recovered tree-sitter facts remain T1
        // candidates rather than semantic claims.
        let has_error =
            tree.root_node().has_error() && syn::parse_file(std::str::from_utf8(&source)?).is_err();
        output.files.push(FileRecord {
            path: relative.clone(),
            has_parse_error: has_error,
        });
        if has_error {
            output.error_files.push(relative.clone());
        }
        let mut stack = vec![(tree.root_node(), None::<String>)];
        while let Some((node, owner)) = stack.pop() {
            let kind = node.kind();
            let mut child_owner = owner.clone();
            if kind == "impl_item" {
                let body = text(&source, node);
                let signature = body.split('{').next().unwrap_or(body).trim().to_string();
                output.facts.push(Fact {
                    path: relative.clone(),
                    kind: "impl".into(),
                    name: signature.clone(),
                    owner: owner.clone(),
                    range: range(node),
                });
                child_owner = Some(signature);
            } else if is_definition(kind) {
                if let Some(name_node) = node.child_by_field_name("name") {
                    let name = text(&source, name_node).to_string();
                    output.facts.push(Fact {
                        path: relative.clone(),
                        kind: kind.into(),
                        name: name.clone(),
                        owner: owner.clone(),
                        range: range(name_node),
                    });
                    if matches!(kind, "trait_item" | "mod_item") {
                        child_owner = Some(format!("{kind} {name}"));
                    }
                }
            } else if kind == "use_declaration" {
                if let Some(argument) = node.child_by_field_name("argument") {
                    output.facts.push(Fact {
                        path: relative.clone(),
                        kind: kind.into(),
                        name: text(&source, argument).into(),
                        owner: owner.clone(),
                        range: range(argument),
                    });
                }
            } else if kind == "macro_invocation" {
                if let Some(macro_node) = node.child_by_field_name("macro") {
                    output.facts.push(Fact {
                        path: relative.clone(),
                        kind: kind.into(),
                        name: text(&source, macro_node).into(),
                        owner: owner.clone(),
                        range: range(macro_node),
                    });
                }
            }
            let mut cursor = node.walk();
            let children: Vec<_> = node.children(&mut cursor).collect();
            for child in children.into_iter().rev() {
                stack.push((child, child_owner.clone()));
            }
        }
    }
    let mut candidate_names: Vec<_> = output
        .facts
        .iter()
        .filter(|fact| !matches!(fact.kind.as_str(), "use_declaration" | "macro_invocation"))
        .map(|fact| fact.name.clone())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect();
    candidate_names.sort();
    let name_indexes: HashMap<_, _> = candidate_names
        .iter()
        .enumerate()
        .map(|(index, name)| (name.as_str(), index))
        .collect();
    let mut owner_indexes = HashMap::<String, usize>::new();
    for (path_index, (_, path)) in paths.iter().enumerate() {
        let source = fs::read(path)?;
        let tree = parser
            .parse(&source, None)
            .ok_or("parser returned no tree")?;
        let mut stack = vec![(tree.root_node(), None::<String>)];
        while let Some((node, owner)) = stack.pop() {
            let kind = node.kind();
            let mut child_owner = owner.clone();
            if matches!(kind, "identifier" | "type_identifier") {
                let name = text(&source, node);
                if let Some(name_index) = name_indexes.get(name) {
                    let owner_index = owner.as_ref().map(|value| {
                        let next = owner_indexes.len();
                        *owner_indexes.entry(value.clone()).or_insert(next)
                    });
                    let start = node.start_position();
                    let end = node.end_position();
                    for value in [
                        path_index,
                        *name_index,
                        owner_index.unwrap_or(u32::MAX as usize),
                        start.row + 1,
                        start.column + 1,
                        end.row + 1,
                        end.column + 1,
                    ] {
                        push_candidate_word(&mut output.identifier_candidates_hex, value)?;
                    }
                    output.identifier_candidate_count += 1;
                }
            }
            if kind == "impl_item" {
                let body = text(&source, node);
                child_owner = Some(body.split('{').next().unwrap_or(body).trim().to_string());
            } else if matches!(kind, "trait_item" | "mod_item") {
                if let Some(name_node) = node.child_by_field_name("name") {
                    child_owner = Some(format!("{kind} {}", text(&source, name_node)));
                }
            }
            let mut cursor = node.walk();
            let children: Vec<_> = node.children(&mut cursor).collect();
            for child in children.into_iter().rev() {
                stack.push((child, child_owner.clone()));
            }
        }
    }
    let mut candidate_owners = vec![String::new(); owner_indexes.len()];
    for (owner, index) in owner_indexes {
        candidate_owners[index] = owner;
    }
    output.candidate_names = candidate_names;
    output.candidate_owners = candidate_owners;
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(output_path, serde_json::to_vec(&output)?)?;
    Ok(())
}
