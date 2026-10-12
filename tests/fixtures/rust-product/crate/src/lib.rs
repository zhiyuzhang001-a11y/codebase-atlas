pub mod left;
pub mod right;

pub fn left_call() { left::run(); }
pub fn right_call() { right::run(); }
pub fn unicode_call() { let _ = "🦀"; left::run(); }
#[cfg(feature = "fast")]
pub fn feature_call() { left::run(); }
