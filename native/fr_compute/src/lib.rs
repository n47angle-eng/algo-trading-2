//! fr_compute — pure numeric kernels. No network, no DB, no IB, no authority writes.
//! Capability: writes_authority=false

#![deny(unsafe_op_in_unsafe_fn)]

mod backtest;
mod chart;
mod trend_strategy;
mod wire;

use std::os::raw::{c_int, c_void};
use std::ptr;
use std::slice;

fn write_json_out(json: String, out_ptr: *mut *mut u8, out_len: *mut usize) -> c_int {
    let buf = json.into_bytes();
    let len = buf.len();
    let mut boxed = buf.into_boxed_slice();
    let ptr = boxed.as_mut_ptr();
    std::mem::forget(boxed);
    unsafe {
        *out_ptr = ptr;
        *out_len = len;
    }
    0
}

/// Compute chart series from a strict JSON request; allocate UTF-8 JSON response.
/// Returns 0 on success; negative on failure. Caller must `fr_string_free` out_ptr.
#[no_mangle]
pub unsafe extern "C" fn fr_chart_compute_v1(
    request_ptr: *const u8,
    request_len: usize,
    out_ptr: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if request_ptr.is_null() || out_ptr.is_null() || out_len.is_null() {
        return -1;
    }
    if request_len == 0 || request_len > 32 * 1024 * 1024 {
        return -2;
    }
    let bytes = unsafe { slice::from_raw_parts(request_ptr, request_len) };
    match chart::compute_from_json_bytes(bytes) {
        Ok(json) => write_json_out(json, out_ptr, out_len),
        Err(_) => -3,
    }
}

/// Create a stateful TrendStrategy handle (tick_size, pullback_ema_period).
#[no_mangle]
pub extern "C" fn fr_trend_strategy_new(tick_size: f64, pullback_ema_period: u32) -> u64 {
    if !tick_size.is_finite() || tick_size <= 0.0 {
        return 0;
    }
    if pullback_ema_period != 18 && pullback_ema_period != 90 {
        return 0;
    }
    trend_strategy::strategy_new(tick_size, pullback_ema_period)
}

#[no_mangle]
pub unsafe extern "C" fn fr_trend_strategy_on_entry_bar(
    handle: u64,
    request_ptr: *const u8,
    request_len: usize,
    out_ptr: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if handle == 0 || request_ptr.is_null() || out_ptr.is_null() || out_len.is_null() {
        return -1;
    }
    if request_len == 0 || request_len > 8 * 1024 * 1024 {
        return -2;
    }
    let bytes = unsafe { slice::from_raw_parts(request_ptr, request_len) };
    match trend_strategy::strategy_on_entry_bar(handle, bytes) {
        Ok(json) => write_json_out(json, out_ptr, out_len),
        Err(_) => -3,
    }
}

#[no_mangle]
pub unsafe extern "C" fn fr_trend_strategy_end_day(
    handle: u64,
    request_ptr: *const u8,
    request_len: usize,
    out_ptr: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if handle == 0 || request_ptr.is_null() || out_ptr.is_null() || out_len.is_null() {
        return -1;
    }
    let bytes = unsafe { slice::from_raw_parts(request_ptr, request_len) };
    match trend_strategy::strategy_end_day(handle, bytes) {
        Ok(json) => write_json_out(json, out_ptr, out_len),
        Err(_) => -3,
    }
}

#[no_mangle]
pub unsafe extern "C" fn fr_trend_strategy_state(
    handle: u64,
    request_ptr: *const u8,
    request_len: usize,
    out_ptr: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if handle == 0 || out_ptr.is_null() || out_len.is_null() {
        return -1;
    }
    let _ = (request_ptr, request_len);
    match trend_strategy::strategy_state(handle) {
        Ok(json) => write_json_out(json, out_ptr, out_len),
        Err(_) => -3,
    }
}

/// Closed-bar dual-EMA backtest draft loop. Same ABI pattern as chart compute.
#[no_mangle]
pub unsafe extern "C" fn fr_backtest_loop_v1(
    request_ptr: *const u8,
    request_len: usize,
    out_ptr: *mut *mut u8,
    out_len: *mut usize,
) -> c_int {
    if request_ptr.is_null() || out_ptr.is_null() || out_len.is_null() {
        return -1;
    }
    if request_len == 0 || request_len > 32 * 1024 * 1024 {
        return -2;
    }
    let bytes = unsafe { slice::from_raw_parts(request_ptr, request_len) };
    match backtest::compute_from_json_bytes(bytes) {
        Ok(json) => write_json_out(json, out_ptr, out_len),
        Err(_) => -3,
    }
}

/// Free a buffer returned by `fr_chart_compute_v1`.
#[no_mangle]
pub unsafe extern "C" fn fr_string_free(ptr: *mut u8, len: usize) {
    if ptr.is_null() || len == 0 {
        return;
    }
    unsafe {
        let _ = Vec::from_raw_parts(ptr, len, len);
    }
}

/// Compile-time marker: this crate does not write authority.
#[no_mangle]
pub extern "C" fn fr_writes_authority() -> c_int {
    0
}

/// Ensure unused import silence in some builds.
#[allow(dead_code)]
fn _touch() {
    let _ = ptr::null::<c_void>();
    let _ = wire::reject_non_finite(0.0);
}
