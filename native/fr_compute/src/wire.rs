//! Strict finite JSON helpers.

pub fn reject_non_finite(v: f64) -> Result<f64, &'static str> {
    if v.is_finite() {
        Ok(v)
    } else {
        Err("non_finite")
    }
}
