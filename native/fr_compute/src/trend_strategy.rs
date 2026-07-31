//! trend_strategy_v1 — bit-exact closed-bar P1 TrendStrategy port.
//! Mirrors futures_research.backtest.strategy TrendStrategy / PullbackLifecycle.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Mutex;

// ---- time helpers (same as chart.rs) ----
fn parse_utc_epoch(value: &str) -> Result<i64, String> {
    let cleaned = value.trim_end_matches('Z');
    let (date, time) = cleaned.split_once('T').ok_or("bad timestamp")?;
    let mut d = date.split('-');
    let y: i32 = d.next().ok_or("y")?.parse().map_err(|_| "y")?;
    let mo: u32 = d.next().ok_or("m")?.parse().map_err(|_| "m")?;
    let da: u32 = d.next().ok_or("d")?.parse().map_err(|_| "d")?;
    let mut t = time.split(':');
    let hh: u32 = t.next().ok_or("h")?.parse().map_err(|_| "h")?;
    let mm: u32 = t.next().ok_or("min")?.parse().map_err(|_| "min")?;
    let ss_part = t.next().unwrap_or("0");
    let ss: u32 = ss_part.split('.').next().unwrap_or("0").parse().map_err(|_| "s")?;
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

#[derive(Clone, Debug, Serialize, Deserialize)]
struct BarSnap {
    timeframe: String,
    timestamp: String,
    ts_init: String,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: i64,
    source_count: i64,
    ema_18: f64,
    ema_50: f64,
    ema_90: f64,
    atr_14: f64,
    is_ready: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct DailyIn {
    regime: String,
    direction: String,
    normalized_separation: Option<f64>,
    normalized_slope: Option<f64>,
    recent_average_true_range: Option<f64>,
    snapshot: BarSnap,
}

#[derive(Clone, Debug)]
struct Pending {
    kind: String,
    direction: String,
    entry_price: f64,
    stop_price: f64,
    source_timestamp: String,
    source_ts_init: String,
    inside_count: Option<i64>,
    stop_reference_type: Option<String>,
    stop_reference_price: Option<f64>,
    stop_offset_ticks: i64,
}

#[derive(Clone, Debug)]
struct Lifecycle {
    state: String, // idle, awaiting_touch, awaiting_signal, completed, exhausted
    direction: String,
    swing_reference: Option<f64>,
    pullback_ema_period: u32,
}

impl Lifecycle {
    fn new(period: u32) -> Self {
        Self {
            state: "idle".into(),
            direction: "none".into(),
            swing_reference: None,
            pullback_ema_period: period,
        }
    }
    fn is_first_pullback_qualified(&self) -> bool {
        self.state == "awaiting_signal"
    }
    fn touch_ema(&self, s: &BarSnap) -> f64 {
        if self.pullback_ema_period == 18 {
            s.ema_18
        } else {
            s.ema_90
        }
    }
    fn arm_from_cross(&mut self, direction: &str, bar: &BarSnap) -> Vec<Value> {
        let mut out = Vec::new();
        if self.state == "awaiting_touch" || self.state == "awaiting_signal" {
            out.push(self.cancel_for_cross_reversal());
        }
        let prev = self.state.clone();
        self.state = "awaiting_touch".into();
        self.direction = direction.into();
        self.swing_reference = Some(if direction == "long" { bar.high } else { bar.low });
        out.push(json!({
            "event_type": "pullback_armed",
            "from_state": prev,
            "to_state": self.state,
            "direction": direction,
            "reason": "ema_cross",
            "price": self.swing_reference,
        }));
        out
    }
    fn cancel_for_cross_reversal(&mut self) -> Value {
        let prev = self.state.clone();
        let prev_dir = self.direction.clone();
        self.state = "idle".into();
        self.direction = "none".into();
        self.swing_reference = None;
        json!({
            "event_type": "pullback_invalidated",
            "from_state": prev,
            "to_state": "idle",
            "direction": prev_dir,
            "reason": "ema_cross_reversal",
            "price": null,
        })
    }
    fn exhaust(&mut self, reason: &str, price: f64) -> Value {
        let prev = self.state.clone();
        self.state = "exhausted".into();
        json!({
            "event_type": "pullback_exhausted",
            "from_state": prev,
            "to_state": "exhausted",
            "direction": self.direction,
            "reason": reason,
            "price": price,
        })
    }
    fn touch(&mut self, price: f64) -> Value {
        let prev = self.state.clone();
        self.state = "awaiting_signal".into();
        json!({
            "event_type": "pullback_touched",
            "from_state": prev,
            "to_state": "awaiting_signal",
            "direction": self.direction,
            "reason": format!("ema_{}_touched", self.pullback_ema_period),
            "price": price,
        })
    }
    fn complete_entry(&mut self) -> Value {
        let prev = self.state.clone();
        self.state = "completed".into();
        json!({
            "event_type": "pullback_completed",
            "from_state": prev,
            "to_state": "completed",
            "direction": self.direction,
            "reason": "entry_intent_created",
            "price": self.swing_reference,
        })
    }
    fn observe_closed_bar(&mut self, s: &BarSnap) -> Vec<Value> {
        if self.state != "awaiting_touch" && self.state != "awaiting_signal" {
            return vec![];
        }
        let swing = self.swing_reference.unwrap();
        let bar = s;
        let touch_ema = self.touch_ema(s);
        if self.state == "awaiting_touch" {
            if self.direction == "long" {
                if bar.low <= touch_ema {
                    if bar.high > swing {
                        return vec![self.exhaust("touch_and_swing_reclaim_same_bar", bar.high)];
                    }
                    return vec![self.touch(bar.low)];
                }
                self.swing_reference = Some(swing.max(bar.high));
                return vec![];
            }
            if self.direction == "short" {
                if bar.high >= touch_ema {
                    if bar.low < swing {
                        return vec![self.exhaust("touch_and_swing_reclaim_same_bar", bar.low)];
                    }
                    return vec![self.touch(bar.high)];
                }
                self.swing_reference = Some(swing.min(bar.low));
                return vec![];
            }
        }
        if self.direction == "long" && bar.high > swing {
            return vec![self.exhaust("swing_reclaimed_before_entry", bar.high)];
        }
        if self.direction == "short" && bar.low < swing {
            return vec![self.exhaust("swing_reclaimed_before_entry", bar.low)];
        }
        vec![]
    }
    fn observe_intrabar_invalidation(&mut self, bar: &BarSnap) -> Option<Value> {
        if self.state != "awaiting_signal" {
            return None;
        }
        let swing = self.swing_reference.unwrap();
        if self.direction == "long" && bar.high > swing {
            return Some(self.exhaust("swing_reclaimed_before_entry", bar.high));
        }
        if self.direction == "short" && bar.low < swing {
            return Some(self.exhaust("swing_reclaimed_before_entry", bar.low));
        }
        None
    }
}

struct Strategy {
    tick_size: f64,
    entry_lc: Lifecycle,
    mid_lc: Lifecycle,
    events: Vec<Value>,
    event_sequence: i64,
    previous_entry: Option<BarSnap>,
    previous_mid: Option<BarSnap>,
    inside_mother: Option<BarSnap>,
    inside_count: i64,
    pending: Option<Pending>,
    daily_regime: Option<String>,
    daily_direction: Option<String>,
    daily_close: Option<String>,
    mid_direction: String,
    mid_context_close: Option<String>,
    entry_locked: bool,
    signal_eval_seq: i64,
    enable_inside: bool,
    enable_magic: bool,
    stop_offset_ticks: i64,
}

impl Strategy {
    fn new(tick_size: f64, pullback_ema_period: u32) -> Self {
        Self {
            tick_size,
            entry_lc: Lifecycle::new(pullback_ema_period),
            mid_lc: Lifecycle::new(pullback_ema_period),
            events: vec![],
            event_sequence: 0,
            previous_entry: None,
            previous_mid: None,
            inside_mother: None,
            inside_count: 0,
            pending: None,
            daily_regime: None,
            daily_direction: None,
            daily_close: None,
            mid_direction: "none".into(),
            mid_context_close: None,
            entry_locked: false,
            signal_eval_seq: 0,
            enable_inside: true,
            enable_magic: true,
            stop_offset_ticks: 1,
        }
    }

    fn emit(
        &mut self,
        bar_ts: &str,
        bar_tsi: &str,
        phase: &str,
        machine: &str,
        event_type: &str,
        from_state: Option<&str>,
        to_state: Option<&str>,
        direction: &str,
        price: Option<f64>,
        details: Value,
    ) -> Value {
        self.event_sequence += 1;
        let ev = json!({
            "sequence": self.event_sequence,
            "timestamp": bar_ts,
            "ts_init": bar_tsi,
            "phase": phase,
            "machine": machine,
            "event_type": event_type,
            "from_state": from_state,
            "to_state": to_state,
            "direction": direction,
            "price": price,
            "details": details,
            "origin_sequence": self.event_sequence,
        });
        self.events.push(ev.clone());
        ev
    }

    fn emit_lc(&mut self, transitions: Vec<Value>, bar: &BarSnap, phase: &str, machine: &str) {
        for t in transitions {
            self.emit(
                &bar.timestamp,
                &bar.ts_init,
                phase,
                machine,
                t["event_type"].as_str().unwrap(),
                t["from_state"].as_str(),
                t["to_state"].as_str(),
                t["direction"].as_str().unwrap_or("none"),
                t.get("price").and_then(|p| p.as_f64()),
                json!({"reason": t["reason"]}),
            );
        }
    }

    fn direction_from(s: &BarSnap) -> String {
        if !s.is_ready {
            return "none".into();
        }
        if s.ema_18 > s.ema_90 {
            "long".into()
        } else if s.ema_18 < s.ema_90 {
            "short".into()
        } else {
            "none".into()
        }
    }

    fn cross_direction(prev: &Option<BarSnap>, cur: &BarSnap) -> String {
        let Some(p) = prev else { return "none".into() };
        if !p.is_ready || !cur.is_ready {
            return "none".into();
        }
        if cur.ema_18 > cur.ema_90 && p.ema_18 <= p.ema_90 {
            return "long".into();
        }
        if cur.ema_18 < cur.ema_90 && p.ema_18 >= p.ema_90 {
            return "short".into();
        }
        "none".into()
    }

    fn is_inside(cur: &BarSnap, mother: &BarSnap) -> bool {
        cur.high < mother.high && cur.low > mother.low
    }

    fn is_magic(cur: &BarSnap, prev: &BarSnap, direction: &str) -> bool {
        let mid = (cur.high + cur.low) / 2.0;
        if direction == "long" {
            cur.close > mid && cur.low < prev.low
        } else if direction == "short" {
            cur.close < mid && cur.high > prev.high
        } else {
            false
        }
    }

    fn gate_allows(&self, direction: &str) -> bool {
        !self.entry_locked
            && self.daily_regime.as_deref() == Some("trend")
            && self.mid_direction == direction
            && self.mid_lc.is_first_pullback_qualified()
            && direction != "none"
    }

    fn on_entry_bar(&mut self, entry: BarSnap, daily: Option<DailyIn>, mid: Option<BarSnap>) -> Value {
        let mut update_events = Vec::new();
        let mut intents = Vec::new();
        // pending intrabar
        self.process_pending_intrabar(&entry, &mut update_events, &mut intents);
        // daily close
        if let Some(d) = daily {
            let new_close = d.snapshot.ts_init.clone();
            let should = match &self.daily_close {
                None => true,
                Some(c) => parse_utc_epoch(&new_close).unwrap_or(0) > parse_utc_epoch(c).unwrap_or(0),
            };
            if should {
                let prev = self.daily_regime.clone();
                self.daily_regime = Some(d.regime.clone());
                self.daily_direction = Some(d.direction.clone());
                self.daily_close = Some(new_close);
                if prev.as_deref() != Some(d.regime.as_str()) {
                    let ev = self.emit(
                        &entry.timestamp,
                        &entry.ts_init,
                        "close",
                        "daily_regime",
                        "daily_regime_changed",
                        prev.as_deref(),
                        Some(d.regime.as_str()),
                        &d.direction,
                        None,
                        json!({"source_close": d.snapshot.ts_init}),
                    );
                    update_events.push(ev);
                }
            }
        }
        // mid close
        if let Some(m) = mid {
            let should = match &self.mid_context_close {
                None => true,
                Some(c) => parse_utc_epoch(&m.ts_init).unwrap_or(0) > parse_utc_epoch(c).unwrap_or(0),
            };
            if should {
                let prev_dir = self.mid_direction.clone();
                self.mid_context_close = Some(m.ts_init.clone());
                self.mid_direction = Self::direction_from(&m);
                if prev_dir != self.mid_direction {
                    let ev = self.emit(
                        &entry.timestamp,
                        &entry.ts_init,
                        "close",
                        "mid_direction",
                        "mid_direction_changed",
                        Some(&prev_dir),
                        Some(&self.mid_direction.clone()),
                        &self.mid_direction.clone(),
                        None,
                        json!({"source_close": m.ts_init}),
                    );
                    update_events.push(ev);
                }
                let cross = Self::cross_direction(&self.previous_mid, &m);
                let transitions = if cross != "none" {
                    self.mid_lc.arm_from_cross(&cross, &m)
                } else {
                    self.mid_lc.observe_closed_bar(&m)
                };
                let before = self.events.len();
                self.emit_lc(transitions, &m, "close", "mid_pullback");
                update_events.extend(self.events[before..].iter().cloned());
                self.previous_mid = Some(m);
            }
        }
        // clear pending if mid pullback invalidated
        if self.pending.is_some() && !self.mid_lc.is_first_pullback_qualified() {
            self.clear_pending(&entry.timestamp, &entry.ts_init, "close", "mid_pullback_invalidated", &mut update_events);
        }
        if !self.entry_locked {
            self.process_entry_close(&entry, &mut update_events);
        }
        self.previous_entry = Some(entry);
        json!({
            "events": update_events,
            "entry_intents": intents,
        })
    }

    fn clear_pending(&mut self, ts: &str, tsi: &str, phase: &str, reason: &str, update: &mut Vec<Value>) {
        let Some(p) = self.pending.take() else { return };
        let price = if reason == "oco_stop_touched" { Some(p.stop_price) } else { None };
        let ev = self.emit(
            ts, tsi, phase, "entry_signal", "signal_cancelled",
            Some("pending"), None, &p.direction, price,
            json!({"reason": reason, "signal_kind": p.kind}),
        );
        update.push(ev);
    }

    fn process_pending_intrabar(&mut self, entry: &BarSnap, update: &mut Vec<Value>, intents: &mut Vec<Value>) {
        let Some(pending) = self.pending.clone() else { return };
        if self.entry_locked { return; }
        if let Some(inv) = self.entry_lc.observe_intrabar_invalidation(entry) {
            let before = self.events.len();
            self.emit_lc(vec![inv], entry, "intrabar", "entry_pullback");
            update.extend(self.events[before..].iter().cloned());
            self.clear_pending(&entry.timestamp, &entry.ts_init, "intrabar", "pullback_exhausted", update);
            return;
        }
        if !self.gate_allows(&pending.direction) {
            self.clear_pending(&entry.timestamp, &entry.ts_init, "intrabar", "daily_or_mid_gate_changed", update);
            return;
        }
        let stop_hit = if pending.direction == "long" { entry.low <= pending.stop_price } else { entry.high >= pending.stop_price };
        if stop_hit {
            self.clear_pending(&entry.timestamp, &entry.ts_init, "intrabar", "oco_stop_touched", update);
            return;
        }
        let entry_hit = if pending.direction == "long" { entry.high >= pending.entry_price } else { entry.low <= pending.entry_price };
        if !entry_hit { return; }

        let ev = self.emit(
            &entry.timestamp, &entry.ts_init, "intrabar", "entry_signal", "entry_intent_created",
            Some("pending"), Some("triggered"), &pending.direction, Some(pending.entry_price),
            json!({"signal_kind": pending.kind, "stop_reference": pending.stop_price}),
        );
        update.push(ev);
        intents.push(json!({
            "direction": pending.direction,
            "entry_reference": pending.entry_price,
            "stop_reference": pending.stop_price,
            "signal_kind": pending.kind,
            "signal_timestamp": pending.source_timestamp,
            "timestamp": entry.timestamp,
            "ts_init": entry.ts_init,
        }));
        self.pending = None;
        self.entry_locked = true;
        let before = self.events.len();
        let t1 = self.entry_lc.complete_entry();
        self.emit_lc(vec![t1], entry, "intrabar", "entry_pullback");
        let t2 = self.mid_lc.complete_entry();
        self.emit_lc(vec![t2], entry, "intrabar", "mid_pullback");
        update.extend(self.events[before..].iter().cloned());
    }

    fn process_entry_close(&mut self, entry: &BarSnap, update: &mut Vec<Value>) {
        // inside run
        let mut inside_run: Option<(BarSnap, i64)> = None;
        if let Some(prev) = &self.previous_entry {
            if self.inside_mother.is_some() && Self::is_inside(entry, self.inside_mother.as_ref().unwrap()) {
                self.inside_count += 1;
                inside_run = Some((self.inside_mother.clone().unwrap(), self.inside_count));
            } else if Self::is_inside(entry, prev) {
                self.inside_mother = Some(prev.clone());
                self.inside_count = 1;
                inside_run = Some((prev.clone(), 1));
            } else {
                self.inside_mother = None;
                self.inside_count = 0;
            }
        } else {
            self.inside_mother = None;
            self.inside_count = 0;
        }

        let cross = Self::cross_direction(&self.previous_entry, entry);
        if cross != "none" {
            self.clear_pending(&entry.timestamp, &entry.ts_init, "close", "ema_cross_reversal", update);
            let before = self.events.len();
            let tr = self.entry_lc.arm_from_cross(&cross, entry);
            self.emit_lc(tr, entry, "close", "entry_pullback");
            update.extend(self.events[before..].iter().cloned());
            return;
        }
        let was_ready = self.entry_lc.state == "awaiting_signal";
        let before = self.events.len();
        let tr = self.entry_lc.observe_closed_bar(entry);
        let exhausted = tr.iter().any(|t| t["event_type"] == "pullback_exhausted");
        self.emit_lc(tr, entry, "close", "entry_pullback");
        update.extend(self.events[before..].iter().cloned());
        if exhausted {
            self.clear_pending(&entry.timestamp, &entry.ts_init, "close", "pullback_exhausted", update);
            return;
        }
        if was_ready && self.entry_lc.state == "awaiting_signal" {
            self.try_create_signal(entry, inside_run, update);
        }
    }

    fn try_create_signal(&mut self, entry: &BarSnap, inside_run: Option<(BarSnap, i64)>, update: &mut Vec<Value>) {
        let direction = self.entry_lc.direction.clone();
        let mut candidates: Vec<Pending> = vec![];
        if self.enable_inside {
            if let Some((mother, count)) = inside_run {
                let stop_anchor = if direction == "long" { mother.low } else { mother.high };
                candidates.push(Pending {
                    kind: "inside".into(),
                    direction: direction.clone(),
                    entry_price: if direction == "long" { mother.high } else { mother.low },
                    stop_price: if direction == "long" { stop_anchor - self.tick_size } else { stop_anchor + self.tick_size },
                    source_timestamp: entry.timestamp.clone(),
                    source_ts_init: entry.ts_init.clone(),
                    inside_count: Some(count),
                    stop_reference_type: Some(if direction == "long" { "mother_low".into() } else { "mother_high".into() }),
                    stop_reference_price: Some(stop_anchor),
                    stop_offset_ticks: self.stop_offset_ticks,
                });
            }
        }
        if self.enable_magic {
            if let Some(prev) = &self.previous_entry {
                if Self::is_magic(entry, prev, &direction) {
                    let stop_anchor = if direction == "long" { entry.low } else { entry.high };
                    candidates.push(Pending {
                        kind: "magic".into(),
                        direction: direction.clone(),
                        entry_price: if direction == "long" { entry.high } else { entry.low },
                        stop_price: if direction == "long" { stop_anchor - self.tick_size } else { stop_anchor + self.tick_size },
                        source_timestamp: entry.timestamp.clone(),
                        source_ts_init: entry.ts_init.clone(),
                        inside_count: None,
                        stop_reference_type: Some(if direction == "long" { "signal_low".into() } else { "signal_high".into() }),
                        stop_reference_price: Some(stop_anchor),
                        stop_offset_ticks: self.stop_offset_ticks,
                    });
                }
            }
        }
        if candidates.is_empty() { return; }

        let cross_dir = Self::direction_from(entry);
        if cross_dir != direction {
            let ev = self.emit(
                &entry.timestamp, &entry.ts_init, "close", "entry_signal", "signal_rejected",
                Some("awaiting_signal"), Some("awaiting_signal"), &direction, None,
                json!({"reason": "entry_cross_state_mismatch"}),
            );
            update.push(ev);
            return;
        }
        if !self.gate_allows(&direction) {
            let reason = if self.daily_regime.is_none() {
                "daily_regime_unavailable".to_string()
            } else if self.daily_regime.as_deref() != Some("trend") {
                format!("daily_regime_{}", self.daily_regime.as_deref().unwrap_or("?"))
            } else if self.mid_direction != direction {
                "mid_entry_direction_mismatch".into()
            } else if !self.mid_lc.is_first_pullback_qualified() {
                "mid_pullback_not_qualified".into()
            } else {
                "entry_locked".into()
            };
            let ev = self.emit(
                &entry.timestamp, &entry.ts_init, "close", "entry_signal", "signal_rejected",
                Some("awaiting_signal"), Some("awaiting_signal"), &direction, None,
                json!({"reason": reason}),
            );
            update.push(ev);
            return;
        }
        if candidates.len() > 1 {
            let kinds: Vec<String> = candidates.iter().map(|c| c.kind.clone()).collect();
            let ev = self.emit(
                &entry.timestamp, &entry.ts_init, "close", "entry_signal", "signal_rejected",
                Some("awaiting_signal"), Some("awaiting_signal"), &direction, None,
                json!({"reason": "multiple_signal_bars_same_bar", "candidates": kinds}),
            );
            update.push(ev);
            return;
        }
        let signal = candidates.into_iter().next().unwrap();
        let ev = self.emit(
            &entry.timestamp, &entry.ts_init, "close", "entry_signal", "signal_created",
            None, Some("pending"), &signal.direction, Some(signal.entry_price),
            json!({
                "signal_kind": signal.kind,
                "stop_reference": signal.stop_price,
                "inside_count": signal.inside_count.unwrap_or(0),
            }),
        );
        update.push(ev);
        self.pending = Some(signal);
    }

    fn end_day(&mut self, at: &str) -> Value {
        let mut update = Vec::new();
        self.clear_pending(at, at, "day_end", "day_end_clear", &mut update);
        if self.entry_locked {
            self.entry_locked = false;
            let ev = self.emit(at, at, "day_end", "entry_lock", "day_reset", Some("locked"), Some("ready"), "none", None,
                json!({"reason": "day_end_forced_flat_boundary"}));
            update.push(ev);
        }
        json!({"events": update, "entry_intents": []})
    }

    fn state_json(&self) -> Value {
        let pending = self.pending.as_ref().map(|p| json!({
            "kind": p.kind,
            "direction": p.direction,
            "entry_price": p.entry_price,
            "stop_price": p.stop_price,
            "source_timestamp": p.source_timestamp,
            "source_ts_init": p.source_ts_init,
            "inside_count": p.inside_count,
            "stop_reference_type": p.stop_reference_type,
            "stop_reference_price": p.stop_reference_price,
            "stop_offset_ticks": p.stop_offset_ticks,
        }));
        json!({
            "pullback_state": self.entry_lc.state,
            "pullback_direction": self.entry_lc.direction,
            "mid_pullback_state": self.mid_lc.state,
            "mid_pullback_direction": self.mid_lc.direction,
            "pending_signal": pending,
            "event_log": self.events,
        })
    }
}


// Manual registry without lazy_static crate
static REG: Mutex<Option<HashMap<u64, Strategy>>> = Mutex::new(None);
static NEXT: Mutex<u64> = Mutex::new(1);

fn registry() -> std::sync::MutexGuard<'static, Option<HashMap<u64, Strategy>>> {
    let mut g = REG.lock().unwrap();
    if g.is_none() {
        *g = Some(HashMap::new());
    }
    g
}

pub fn strategy_new(tick_size: f64, pullback_ema_period: u32) -> u64 {
    let mut map = registry();
    let id = {
        let mut n = NEXT.lock().unwrap();
        let id = *n;
        *n += 1;
        id
    };
    map.as_mut().unwrap().insert(id, Strategy::new(tick_size, pullback_ema_period));
    id
}

pub fn strategy_on_entry_bar(handle: u64, bytes: &[u8]) -> Result<String, String> {
    let v: Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    let entry: BarSnap = serde_json::from_value(v["entry"].clone()).map_err(|e| e.to_string())?;
    let daily: Option<DailyIn> = if v.get("daily").map(|d| !d.is_null()).unwrap_or(false) {
        Some(serde_json::from_value(v["daily"].clone()).map_err(|e| e.to_string())?)
    } else { None };
    let mid: Option<BarSnap> = if v.get("mid").map(|d| !d.is_null()).unwrap_or(false) {
        Some(serde_json::from_value(v["mid"].clone()).map_err(|e| e.to_string())?)
    } else { None };
    let mut map = registry();
    let st = map.as_mut().unwrap().get_mut(&handle).ok_or("bad handle")?;
    let out = st.on_entry_bar(entry, daily, mid);
    Ok(out.to_string())
}

pub fn strategy_end_day(handle: u64, bytes: &[u8]) -> Result<String, String> {
    let v: Value = serde_json::from_slice(bytes).map_err(|e| e.to_string())?;
    let at = v["at"].as_str().ok_or("at")?.to_string();
    let mut map = registry();
    let st = map.as_mut().unwrap().get_mut(&handle).ok_or("bad handle")?;
    Ok(st.end_day(&at).to_string())
}

pub fn strategy_state(handle: u64) -> Result<String, String> {
    let map = registry();
    let st = map.as_ref().unwrap().get(&handle).ok_or("bad handle")?;
    Ok(st.state_json().to_string())
}

// silence unused
#[allow(dead_code)]
fn _epoch_helpers() {
    let _ = epoch_to_utc_z(0);
}
