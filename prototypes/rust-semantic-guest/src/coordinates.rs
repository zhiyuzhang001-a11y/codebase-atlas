//! Prototype-only public Unicode-codepoint / upstream UTF-8 byte conversion.
//! Not wired into Atlas, compiled, or qualified yet.

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CoordinateError {
    OutsideFile,
    InsideCharacterOrTerminator,
    Overflow,
}

// Match the existing public provider's Python str.splitlines() vocabulary.
// Keep byte spans, never normalize the admitted source text.
fn line_spans(source: &str) -> Vec<(usize, usize)> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = source.char_indices().peekable();
    while let Some((offset, ch)) = chars.next() {
        if matches!(ch, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}'..='\u{1e}'
            | '\u{85}' | '\u{2028}' | '\u{2029}')
        {
            lines.push((start, offset));
            start = offset + ch.len_utf8();
            if ch == '\r' && chars.peek().is_some_and(|(_, next)| *next == '\n') {
                let (offset, next) = chars.next().expect("peeked CRLF continuation");
                start = offset + next.len_utf8();
            }
        }
    }
    if start < source.len() {
        lines.push((start, source.len()));
    }
    lines
}

/// Query coordinates are one-based and match the current public query contract.
/// A final empty line after a terminator is not a valid query line.
pub fn query_byte_offset(source: &str, line: u32, column: u32) -> Result<u32, CoordinateError> {
    let index = line.checked_sub(1).ok_or(CoordinateError::OutsideFile)? as usize;
    let codepoints = column.checked_sub(1).ok_or(CoordinateError::OutsideFile)? as usize;
    let spans = line_spans(source);
    let &(start, end) = spans.get(index).ok_or(CoordinateError::OutsideFile)?;
    let text = &source[start..end];
    let relative = if codepoints == text.chars().count() {
        text.len()
    } else {
        text.char_indices().nth(codepoints).map(|(offset, _)| offset)
            .ok_or(CoordinateError::OutsideFile)?
    };
    u32::try_from(start + relative).map_err(|_| CoordinateError::Overflow)
}

/// Result coordinates accept the provider's extra EOF line when source ends LF.
/// Byte offsets in a UTF-8 character or inside CRLF are rejected, not rounded.
pub fn result_position(source: &str, offset: u32) -> Result<(u32, u32), CoordinateError> {
    let offset = offset as usize;
    if offset > source.len() {
        return Err(CoordinateError::OutsideFile);
    }
    if !source.is_char_boundary(offset) {
        return Err(CoordinateError::InsideCharacterOrTerminator);
    }
    let mut spans = line_spans(source);
    if source.ends_with('\n') {
        spans.push((source.len(), source.len()));
    }
    for (index, (start, end)) in spans.into_iter().enumerate() {
        if start <= offset && offset <= end {
            return Ok((
                u32::try_from(index + 1).map_err(|_| CoordinateError::Overflow)?,
                u32::try_from(source[start..offset].chars().count() + 1)
                    .map_err(|_| CoordinateError::Overflow)?,
            ));
        }
    }
    Err(CoordinateError::InsideCharacterOrTerminator)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unicode_and_crlf_preserve_original_byte_offsets() {
        let text = "a🦀e\u{301}\r\nleft::run();\n";
        assert_eq!(query_byte_offset(text, 1, 3), Ok(5));
        assert_eq!(query_byte_offset(text, 1, 5), Ok(8));
        assert_eq!(query_byte_offset(text, 2, 7), Ok(16));
        assert_eq!(result_position(text, 5), Ok((1, 3)));
        assert_eq!(result_position(text, 16), Ok((2, 7)));
        assert_eq!(result_position(text, 2), Err(CoordinateError::InsideCharacterOrTerminator));
        assert_eq!(result_position(text, 9), Err(CoordinateError::InsideCharacterOrTerminator));
        assert_eq!(query_byte_offset(text, 3, 1), Err(CoordinateError::OutsideFile));
        assert_eq!(result_position(text, text.len() as u32), Ok((3, 1)));
    }

    #[test]
    fn reject_zero_and_out_of_range_without_saturating() {
        assert_eq!(query_byte_offset("run", 0, 1), Err(CoordinateError::OutsideFile));
        assert_eq!(query_byte_offset("run", 1, 0), Err(CoordinateError::OutsideFile));
        assert_eq!(query_byte_offset("run", 1, 5), Err(CoordinateError::OutsideFile));
        assert_eq!(query_byte_offset("", 1, 1), Err(CoordinateError::OutsideFile));
        assert_eq!(result_position("run", 4), Err(CoordinateError::OutsideFile));
        assert_eq!(query_byte_offset("a\u{2028}b", 2, 1), Ok(4));
        assert_eq!(result_position("a\u{2028}b", 4), Ok((2, 1)));
    }
}
