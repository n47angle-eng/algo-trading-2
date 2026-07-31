//! chart_compute_v1 — must match Python oracle in chart_kernel_python.py

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

#[derive(Debug, Deserialize)]
struct Request {
    #[serde(rename = "schema")]
    schema: String,
    timeframe_minutes: u32,
    ema_period: u32,
    bars: Vec<Bar>,
    #[serde(default = "default_backend")]
    requested_backend: String,
}

fn default_backend() -> String {
    "auto".into()
}

#[derive(Debug, Deserialize, Clone)]
struct Bar {
    t: String,
    o: f64,
    h: f64,
    l: f64,
    c: f64,
    v: f64,
}

#[derive(Debug, Serialize)]
struct Point {
    t: String,
    o: f64,
    h: f64,
    l: f64,
    c: f64,
    v: f64,
    ema: f64,
}

#[derive(Debug, Serialize)]
struct Draft {
    #[serde(rename = "schema")]
    schema: String,
    timeframe_minutes: u32,
    ema_period: u32,
    points: Vec<Point>,
    artifact_sha256: String,
    provenance: serde_json::Value,
}

pub fn compute_from_json_bytes(bytes: &[u8]) -> Result<String, String> {
    let req: Request = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    if req.schema != "chart_compute_request.v1" {
        return Err("bad schema".into());
    }
    if req.timeframe_minutes == 0 || req.timeframe_minutes > 1440 {
        return Err("bad timeframe".into());
    }
    if req.ema_period == 0 || req.ema_period > 500 {
        return Err("bad ema".into());
    }
    for b in &req.bars {
        for x in [b.o, b.h, b.l, b.c, b.v] {
            if !x.is_finite() {
                return Err("non_finite".into());
            }
        }
    }
    let points = resample_and_ema(&req.bars, req.timeframe_minutes, req.ema_period)?;
    let mut partial = serde_json::json!({
        "schema": "chart_compute_draft.v1",
        "timeframe_minutes": req.timeframe_minutes,
        "ema_period": req.ema_period,
        "points": points,
    });
    // Canonical hash over points payload (schema fields sorted via Value).
    let artifact_body = serde_json::to_vec(&partial).map_err(|e| e.to_string())?;
    let digest = hex_sha256(&artifact_body);
    let draft = Draft {
        schema: "chart_compute_draft.v1".into(),
        timeframe_minutes: req.timeframe_minutes,
        ema_period: req.ema_period,
        points,
        artifact_sha256: digest.clone(),
        provenance: serde_json::json!({
            "schema": "compute_provenance.v1",
            "feature": "chart_compute_v1",
            "requested_backend": req.requested_backend,
            "effective_backend": "rust",
            "fallback_reason": null,
            "artifact_sha256": digest,
            "writes_authority": false,
        }),
    };
    // Ensure partial unused warning silence
    let _ = partial;
    serde_json::to_string(&draft).map_err(|e| e.to_string())
}

fn resample_and_ema(
    bars: &[Bar],
    timeframe_minutes: u32,
    ema_period: u32,
) -> Result<Vec<Point>, String> {
    if bars.is_empty() {
        return Ok(vec![]);
    }
    let bucket_seconds = (timeframe_minutes as i64) * 60;
    let mut buckets: BTreeMap<i64, Vec<&Bar>> = BTreeMap::new();
    for bar in bars {
        let epoch = parse_utc_epoch(&bar.t)?;
        let bucket = epoch - epoch.rem_euclid(bucket_seconds);
        buckets.entry(bucket).or_default().push(bar);
    }
    let mut points = Vec::with_capacity(buckets.len());
    let mut ema: Option<f64> = None;
    let alpha = 2.0 / (f64::from(ema_period) + 1.0);
    for (bucket, group) in buckets {
        let o = group[0].o;
        let h = group
            .iter()
            .map(|b| b.h)
            .fold(f64::NEG_INFINITY, f64::max);
        let l = group.iter().map(|b| b.l).fold(f64::INFINITY, f64::min);
        let c = group[group.len() - 1].c;
        let v: f64 = group.iter().map(|b| b.v).sum();
        let next_ema = match ema {
            None => c,
            Some(prev) => c * alpha + prev * (1.0 - alpha),
        };
        ema = Some(next_ema);
        points.push(Point {
            t: epoch_to_utc_z(bucket),
            o,
            h,
            l,
            c,
            v,
            ema: next_ema,
        });
    }
    Ok(points)
}

fn parse_utc_epoch(value: &str) -> Result<i64, String> {
    // Expect YYYY-MM-DDTHH:MM:SSZ (seconds precision enough for tests)
    let cleaned = value.trim_end_matches('Z');
    let (date, time) = cleaned
        .split_once('T')
        .ok_or_else(|| "bad timestamp".to_string())?;
    let mut d = date.split('-');
    let y: i32 = d.next().ok_or("y")?.parse().map_err(|_| "y")?;
    let mo: u32 = d.next().ok_or("m")?.parse().map_err(|_| "m")?;
    let da: u32 = d.next().ok_or("d")?.parse().map_err(|_| "d")?;
    let mut t = time.split(':');
    let hh: u32 = t.next().ok_or("h")?.parse().map_err(|_| "h")?;
    let mm: u32 = t.next().ok_or("min")?.parse().map_err(|_| "min")?;
    let ss_part = t.next().unwrap_or("0");
    let ss: u32 = ss_part
        .split('.')
        .next()
        .unwrap_or("0")
        .parse()
        .map_err(|_| "s")?;
    // days from civil date (Howard Hinnant algorithm)
    let days = days_from_civil(y, mo as i32, da as i32);
    Ok(days * 86400 + (hh as i64) * 3600 + (mm as i64) * 60 + ss as i64)
}

fn days_from_civil(y: i32, m: i32, d: i32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = (y - era * 400) as u32;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy as u32;
    (era as i64) * 146097 + doe as i64 - 719468
}

fn epoch_to_utc_z(epoch: i64) -> String {
    let days = epoch.div_euclid(86400);
    let sod = epoch.rem_euclid(86400) as u32;
    let (y, m, d) = civil_from_days(days);
    let hh = sod / 3600;
    let mm = (sod % 3600) / 60;
    let ss = sod % 60;
    format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}Z")
}

fn civil_from_days(z: i64) -> (i32, u32, u32) {
    let z = z + 719468;
    let era = z.div_euclid(146097);
    let doe = (z - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i32 + era as i32 * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

fn hex_sha256(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
