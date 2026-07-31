//! backtest_loop_v1 — closed-bar dual-EMA crossover draft loop.
//! Must match Python oracle in backtest/backtest_kernel_python.py
//! Pure compute only: no IB, no database, no authority writes.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

#[derive(Debug, Deserialize)]
struct Request {
    #[serde(rename = "schema")]
    schema: String,
    timeframe_minutes: u32,
    ema_fast: u32,
    ema_slow: u32,
    quantity: u32,
    point_value: f64,
    commission_per_side: f64,
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
struct Trade {
    entry_t: String,
    exit_t: String,
    direction: String,
    entry_price: f64,
    exit_price: f64,
    quantity: u32,
    gross_points: f64,
    net_pnl: f64,
}

#[derive(Debug, Serialize)]
struct EquityPoint {
    t: String,
    equity: f64,
}

#[derive(Debug, Serialize)]
struct Draft {
    #[serde(rename = "schema")]
    schema: String,
    timeframe_minutes: u32,
    ema_fast: u32,
    ema_slow: u32,
    trades: Vec<Trade>,
    equity_curve: Vec<EquityPoint>,
    trade_count: u32,
    net_pnl: f64,
    artifact_sha256: String,
    provenance: serde_json::Value,
}

pub fn compute_from_json_bytes(bytes: &[u8]) -> Result<String, String> {
    let req: Request = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    if req.schema != "backtest_loop_request.v1" {
        return Err("bad schema".into());
    }
    if req.timeframe_minutes == 0 || req.timeframe_minutes > 1440 {
        return Err("bad timeframe".into());
    }
    if req.ema_fast == 0 || req.ema_slow == 0 || req.ema_fast >= req.ema_slow {
        return Err("bad ema periods".into());
    }
    if req.quantity == 0 || req.quantity > 1000 {
        return Err("bad quantity".into());
    }
    if !req.point_value.is_finite() || req.point_value <= 0.0 {
        return Err("bad point_value".into());
    }
    if !req.commission_per_side.is_finite() || req.commission_per_side < 0.0 {
        return Err("bad commission".into());
    }
    for b in &req.bars {
        for x in [b.o, b.h, b.l, b.c, b.v] {
            if !x.is_finite() {
                return Err("non_finite".into());
            }
        }
    }

    let points = resample(&req.bars, req.timeframe_minutes)?;
    let (trades, equity, net) = run_loop(
        &points,
        req.ema_fast,
        req.ema_slow,
        req.quantity,
        req.point_value,
        req.commission_per_side,
    )?;

    let mut partial = serde_json::json!({
        "schema": "backtest_loop_draft.v1",
        "timeframe_minutes": req.timeframe_minutes,
        "ema_fast": req.ema_fast,
        "ema_slow": req.ema_slow,
        "trades": trades,
        "equity_curve": equity,
        "trade_count": trades.len() as u32,
        "net_pnl": net,
    });
    let body = serde_json::to_vec(&partial).map_err(|e| e.to_string())?;
    let digest = hex_sha256(&body);
    let draft = Draft {
        schema: "backtest_loop_draft.v1".into(),
        timeframe_minutes: req.timeframe_minutes,
        ema_fast: req.ema_fast,
        ema_slow: req.ema_slow,
        trade_count: trades.len() as u32,
        net_pnl: net,
        trades,
        equity_curve: equity,
        artifact_sha256: digest.clone(),
        provenance: serde_json::json!({
            "schema": "compute_provenance.v1",
            "feature": "backtest_loop_v1",
            "requested_backend": req.requested_backend,
            "effective_backend": "rust",
            "fallback_reason": null,
            "artifact_sha256": digest,
            "writes_authority": false,
        }),
    };
    let _ = partial;
    serde_json::to_string(&draft).map_err(|e| e.to_string())
}

#[derive(Clone)]
struct Pt {
    t: String,
    c: f64,
}

fn resample(bars: &[Bar], timeframe_minutes: u32) -> Result<Vec<Pt>, String> {
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
    let mut out = Vec::with_capacity(buckets.len());
    for (bucket, group) in buckets {
        let c = group[group.len() - 1].c;
        out.push(Pt {
            t: epoch_to_utc_z(bucket),
            c,
        });
    }
    Ok(out)
}

fn run_loop(
    points: &[Pt],
    ema_fast: u32,
    ema_slow: u32,
    quantity: u32,
    point_value: f64,
    commission_per_side: f64,
) -> Result<(Vec<Trade>, Vec<EquityPoint>, f64), String> {
    if points.is_empty() {
        return Ok((vec![], vec![], 0.0));
    }
    let af = 2.0 / (f64::from(ema_fast) + 1.0);
    let as_ = 2.0 / (f64::from(ema_slow) + 1.0);
    let mut fast: Option<f64> = None;
    let mut slow: Option<f64> = None;
    let mut position: Option<(String, f64)> = None; // entry_t, entry_price long-only
    let mut trades = Vec::new();
    let mut equity_curve = Vec::new();
    let mut realized = 0.0;
    let mut prev_cross: Option<i32> = None; // -1 fast below, +1 fast above

    for (idx, p) in points.iter().enumerate() {
        let f = match fast {
            None => p.c,
            Some(v) => p.c * af + v * (1.0 - af),
        };
        let s = match slow {
            None => p.c,
            Some(v) => p.c * as_ + v * (1.0 - as_),
        };
        fast = Some(f);
        slow = Some(s);

        // Warm-up: need slow period bars before trading.
        if (idx as u32) + 1 < ema_slow {
            equity_curve.push(EquityPoint {
                t: p.t.clone(),
                equity: realized,
            });
            continue;
        }

        let cross = if f > s {
            1
        } else if f < s {
            -1
        } else {
            prev_cross.unwrap_or(0)
        };

        if let Some(prev) = prev_cross {
            if prev <= 0 && cross > 0 && position.is_none() {
                position = Some((p.t.clone(), p.c));
            } else if prev >= 0 && cross < 0 {
                if let Some((entry_t, entry_px)) = position.take() {
                    let gross = p.c - entry_px;
                    let net = gross * point_value * f64::from(quantity)
                        - 2.0 * commission_per_side * f64::from(quantity);
                    realized += net;
                    trades.push(Trade {
                        entry_t,
                        exit_t: p.t.clone(),
                        direction: "long".into(),
                        entry_price: entry_px,
                        exit_price: p.c,
                        quantity,
                        gross_points: gross,
                        net_pnl: net,
                    });
                }
            }
        }
        prev_cross = Some(cross);
        equity_curve.push(EquityPoint {
            t: p.t.clone(),
            equity: realized,
        });
    }

    // Flatten open position at last bar (mark exit).
    if let Some((entry_t, entry_px)) = position.take() {
        if let Some(last) = points.last() {
            let gross = last.c - entry_px;
            let net = gross * point_value * f64::from(quantity)
                - 2.0 * commission_per_side * f64::from(quantity);
            realized += net;
            trades.push(Trade {
                entry_t,
                exit_t: last.t.clone(),
                direction: "long".into(),
                entry_price: entry_px,
                exit_price: last.c,
                quantity,
                gross_points: gross,
                net_pnl: net,
            });
            if let Some(eq) = equity_curve.last_mut() {
                eq.equity = realized;
            }
        }
    }

    Ok((trades, equity_curve, realized))
}

fn parse_utc_epoch(value: &str) -> Result<i64, String> {
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
