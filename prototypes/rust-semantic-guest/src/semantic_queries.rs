//! Uncompiled adapter draft for the fixed upstream ide API, not a matcher.
//! A reviewed admission/ABI layer must construct change and admitted bytes from
//! the SAME exact project/generation before this function can be exposed.

use std::collections::BTreeMap;

use hir::ChangeWithProcMacros;
use ide::{AnalysisHost, FileId, FilePosition, FindAllRefsConfig, GotoDefinitionConfig,
          NavigationTarget, RaFixtureConfig, TextRange};

const MAX_RESULTS: usize = 10_000;

#[derive(Clone, Copy)]
pub(crate) enum QueryKind {
    Definition,
    References,
}

#[derive(Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct Span {
    pub file: u32,
    pub start_byte: u32,
    pub end_byte: u32,
}

pub(crate) enum Reply {
    Definitions(Vec<Span>),
    References { declaration: Span, references: Vec<Span> },
}

fn checked_span(files: &BTreeMap<FileId, String>, file: FileId, range: TextRange)
    -> Result<Span, &'static str>
{
    let text = files.get(&file).ok_or("result outside admitted file namespace")?;
    let start = u32::from(range.start());
    let end = u32::from(range.end());
    if start > end || end as usize > text.len()
        || !text.is_char_boundary(start as usize) || !text.is_char_boundary(end as usize)
    {
        return Err("result outside admitted text or inside UTF-8 character");
    }
    Ok(Span { file: file.index(), start_byte: start, end_byte: end })
}

fn navigation_span(files: &BTreeMap<FileId, String>, nav: &NavigationTarget)
    -> Result<Span, &'static str>
{
    checked_span(files, nav.file_id, nav.focus_or_full_range())
}

pub(crate) fn query(
    change: ChangeWithProcMacros,
    files: &BTreeMap<FileId, String>,
    position: FilePosition,
    kind: QueryKind,
) -> Result<Reply, &'static str> {
    // Native compiler proc-macros for this engine are a separate build-time TCB.
    // Project-provided proc-macro implementations must never enter the guest.
    if change.proc_macros.is_some() {
        return Err("project proc-macro implementations are not admitted");
    }
    let text = files.get(&position.file_id).ok_or("query outside admitted file namespace")?;
    let offset = u32::from(position.offset) as usize;
    if offset > text.len() || !text.is_char_boundary(offset) {
        return Err("query outside admitted text or inside UTF-8 character");
    }
    let mut host = AnalysisHost::new(None);
    // Upstream apply_change diagnostic Instant still requires the separately
    // reviewed pure-WASM adaptation. This draft has NOT been executed.
    host.apply_change(change);
    let analysis = host.analysis();
    for (file, admitted) in files {
        if analysis.file_text(*file).map_err(|_| "cancelled input verification")?.as_ref()
            != admitted.as_str()
        {
            return Err("database bytes differ from admitted source");
        }
    }
    let mut fixture = RaFixtureConfig::default();
    fixture.disable_ra_fixture = true;
    match kind {
        QueryKind::Definition => {
            let result = analysis.goto_definition(position, &GotoDefinitionConfig { ra_fixture: fixture })
                .map_err(|_| "definition cancelled")?.ok_or("definition unavailable")?;
            if result.info.len() > MAX_RESULTS {
                return Err("definition output exceeds bounded result contract");
            }
            let mut spans = result.info.iter().map(|nav| navigation_span(files, nav))
                .collect::<Result<Vec<_>, _>>()?;
            spans.sort();
            spans.dedup();
            if spans.is_empty() {
                return Err("definition unavailable");
            }
            Ok(Reply::Definitions(spans))
        }
        QueryKind::References => {
            let config = FindAllRefsConfig {
                search_scope: None, ra_fixture: fixture, exclude_imports: false, exclude_tests: false,
            };
            let results = analysis.find_all_refs(position, &config)
                .map_err(|_| "reference search cancelled")?.ok_or("references unavailable")?;
            if results.len() != 1 {
                return Err("reference declaration is not unique");
            }
            let result = &results[0];
            let declaration = navigation_span(files,
                &result.declaration.as_ref().ok_or("reference declaration unavailable")?.nav)?;
            let mut references = Vec::new();
            for (file, ranges) in &result.references {
                for (range, _) in ranges {
                    if references.len() >= MAX_RESULTS {
                        return Err("reference output exceeds bounded result contract");
                    }
                    references.push(checked_span(files, *file, *range)?);
                }
            }
            references.sort();
            references.dedup();
            Ok(Reply::References { declaration, references })
        }
    }
}
