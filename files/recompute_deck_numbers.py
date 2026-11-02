"""
Flowledger growth review: recompute the numbers in the deck straight from the raw exports.

Usage:  python recompute_deck_numbers.py [folder_with_the_csvs]
        (defaults to this script's folder, or a ./data folder next to it)

I used this as an independent cross-check on the spreadsheet: same inputs, separate code path.
It prints reconciliation checks first, then the numbers slide by slide.

Periods used throughout
  June baseline : weeks starting 1, 8, 15 and 22 Jun (before demo form v2)
  Since v2      : weeks starting 29 Jun, 6, 13 and 20 Jul
  Mature post   : 29 Jun and 6 Jul only (the post-v2 cohorts with a full 14-day follow-up)
"""
import sys
from pathlib import Path

import pandas as pd

# ---- inputs and assumptions (all visible, none buried in formulas) -------------------------
PIPELINE_PER_QUAL = 12_000   # data guide: potential annual contract value per qualified lead
WIN_RATE_SCENARIO = 0.20     # data guide: historical qualified-to-won, scenario use only
SEO_RETAINER_MONTH = 8_000   # from the CMO's note
WEEKS_PER_MONTH = 52 / 12

PRE = list(pd.date_range("2026-06-01", periods=4, freq="7D"))
POST = list(pd.date_range("2026-06-29", periods=4, freq="7D"))
MATURE_POST = POST[:2]
IMMATURE = POST[2:]
MATURE_ALL = PRE + MATURE_POST
CHANNELS = ["Organic Search", "Paid Search", "Direct", "Referral"]


def find_folder():
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    for cand in (base, base / "data"):
        if (cand / "02_website_funnel.csv").exists():
            return cand
    sys.exit(f"Couldn't find the CSVs in {base} or {base / 'data'}")


def load(folder):
    d = {
        "search": pd.read_csv(folder / "01_search_performance.csv", parse_dates=["week_start"]),
        "funnel": pd.read_csv(folder / "02_website_funnel.csv", parse_dates=["week_start"]),
        "form": pd.read_csv(folder / "03_form_diagnostics.csv", parse_dates=["week_start"]),
        "crm": pd.read_csv(folder / "04_crm_cohorts.csv", parse_dates=["acquired_week"]),
        "paid": pd.read_csv(folder / "05_paid_spend.csv", parse_dates=["week_start"]),
    }
    d["crm"] = d["crm"].rename(columns={"acquired_week": "week_start", "acquisition_channel": "channel"})
    return d


# ---- reconciliation checks --------------------------------------------------------------------
def run_checks(d):
    keys = ["week_start", "channel", "device"]
    f, x, c = d["funnel"], d["form"], d["crm"]
    m = f.merge(x, on=keys).merge(c, on=keys)
    paid_sessions = f[f.channel == "Paid Search"][["week_start", "device", "sessions"]]
    pm = d["paid"].merge(paid_sessions, on=["week_start", "device"])
    mature = c[c.has_full_14_day_followup == "yes"]
    immature = c[c.has_full_14_day_followup == "no"]

    checks = [
        ("Rows line up across funnel, form and CRM (64 each)", len(m) == len(f) == len(x) == len(c) == 64),
        ("Form starts = attempts", (m.form_starts == m.attempts).all()),
        ("Funnel submissions = server successful submissions", (m.form_submissions == m.successful_submissions).all()),
        ("Attempts = success + phone rejections + other abandonment",
         (m.attempts == m.successful_submissions + m.phone_validation_rejections + m.abandoned_other).all()),
        ("Submissions = new CRM leads", (m.form_submissions == m.new_leads).all()),
        ("Paid landing sessions = paid sessions in funnel", len(pm) == 16 and (pm.landing_sessions == pm.sessions).all()),
        ("Pipeline = qualified as of snapshot x $12k",
         (c.pipeline_created_usd_as_of_snapshot == c.qualified_as_of_snapshot * PIPELINE_PER_QUAL).all()),
        ("Mature cohorts: 14-day qualified = qualified at snapshot",
         (mature.qualified_within_14_days == mature.qualified_as_of_snapshot).all()),
        ("Immature cohorts (13, 20 Jul) have blank 14-day field",
         immature.qualified_within_14_days.isna().all()
         and sorted(immature.week_start.unique()) == sorted(pd.to_datetime(IMMATURE))),
    ]
    print("RECONCILIATION")
    for label, ok in checks:
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}")
    return all(ok for _, ok in checks)


# ---- helpers -------------------------------------------------------------------------------
def total(df, col, weeks, **flt):
    q = df[df.week_start.isin(weeks)]
    for k, v in flt.items():
        q = q[q[k] == v]
    return q[col].sum()


def per_week(df, col, weeks, **flt):
    return total(df, col, weeks, **flt) / len(weeks)


def pct(a, b):
    return a / b - 1


def compute(d):
    f, x, c, s, p = d["funnel"], d["form"], d["crm"], d["search"], d["paid"]
    N = {}

    # --- Slide 2: where the drop is -------------------------------------------------------
    N["sessions_wk"] = (per_week(f, "sessions", PRE), per_week(f, "sessions", POST))
    N["search_clicks_wk"] = (per_week(s, "clicks", PRE), per_week(s, "clicks", POST))
    for dev in ["Mobile", "Desktop"]:
        k = dev.lower()
        st_pre, st_post = total(f, "form_starts", PRE, device=dev), total(f, "form_starts", POST, device=dev)
        sb_pre, sb_post = total(f, "form_submissions", PRE, device=dev), total(f, "form_submissions", POST, device=dev)
        N[f"{k}_starts_wk"] = (st_pre / 4, st_post / 4)
        N[f"{k}_requests_wk"] = (sb_pre / 4, sb_post / 4)
        N[f"{k}_completion"] = (sb_pre / st_pre, sb_post / st_post)
    N["all_requests_wk"] = (per_week(f, "form_submissions", PRE), per_week(f, "form_submissions", POST))
    N["mobile_requests_by_channel_wk"] = {
        ch: (per_week(f, "form_submissions", PRE, device="Mobile", channel=ch),
             per_week(f, "form_submissions", POST, device="Mobile", channel=ch))
        for ch in CHANNELS
    }
    N["weekly_requests"] = {
        str(w.date()): {dev: int(f[(f.week_start == w) & (f.device == dev)].form_submissions.sum())
                        for dev in ["Desktop", "Mobile"]}
        for w in PRE + POST
    }

    # --- Slide 3: the mechanism -------------------------------------------------------------
    for dev in ["Mobile", "Desktop"]:
        k = dev.lower()
        att_pre, att_post = total(x, "attempts", PRE, device=dev), total(x, "attempts", POST, device=dev)
        N[f"{k}_phone_rej_rate"] = (total(x, "phone_validation_rejections", PRE, device=dev) / att_pre,
                                    total(x, "phone_validation_rejections", POST, device=dev) / att_post)
        N[f"{k}_other_abandon_rate"] = (total(x, "abandoned_other", PRE, device=dev) / att_pre,
                                        total(x, "abandoned_other", POST, device=dev) / att_post)
    N["mobile_phone_rej_wk"] = (per_week(x, "phone_validation_rejections", PRE, device="Mobile"),
                                per_week(x, "phone_validation_rejections", POST, device="Mobile"))
    N["mobile_phone_rej_total_since_v2"] = int(total(x, "phone_validation_rejections", POST, device="Mobile"))
    N["weekly_mobile_outcomes"] = {
        str(w.date()): {col: int(x[(x.week_start == w) & (x.device == "Mobile")][col].sum())
                        for col in ["successful_submissions", "phone_validation_rejections", "abandoned_other"]}
        for w in PRE + POST
    }
    # Do the mobile leads that still get through qualify differently? (14-day, mature cohorts only)
    for dev in ["Mobile", "Desktop"]:
        k = dev.lower()
        N[f"{k}_qual_rate"] = (
            total(c, "qualified_within_14_days", PRE, device=dev) / total(c, "new_leads", PRE, device=dev),
            total(c, "qualified_within_14_days", MATURE_POST, device=dev) / total(c, "new_leads", MATURE_POST, device=dev),
        )

    # --- Slide 4: the dashboard view vs like-for-like ------------------------------------------------
    rates = {}
    for ch in CHANNELS:
        for dev in ["Desktop", "Mobile"]:
            leads = total(c, "new_leads", MATURE_ALL, channel=ch, device=dev)
            q = total(c, "qualified_within_14_days", MATURE_ALL, channel=ch, device=dev)
            rates[(ch, dev)] = q / leads
    N["qual_rates_mature"] = {f"{ch}|{dev}": r for (ch, dev), r in rates.items()}

    weekly = {}
    for w in PRE + POST:
        cw = c[c.week_start == w]
        row = {
            "leads": int(cw.new_leads.sum()),
            "q14": None if w in IMMATURE else int(cw.qualified_within_14_days.sum()),
            "q_snapshot": int(cw.qualified_as_of_snapshot.sum()),
            "pipeline_snapshot": int(cw.pipeline_created_usd_as_of_snapshot.sum()),
        }
        if w in IMMATURE:
            # Estimate: apply mature 14-day rates by channel x device to this cohort's leads
            row["q14_projected"] = float(sum(r.new_leads * rates[(r.channel, r.device)] for r in cw.itertuples()))
        weekly[str(w.date())] = row
    N["weekly_cohorts"] = weekly

    q14_pre = total(c, "qualified_within_14_days", PRE) / 4
    q14_post = total(c, "qualified_within_14_days", MATURE_POST) / 2
    N["q14_wk"] = (q14_pre, q14_post)
    N["pipeline14_wk"] = (q14_pre * PIPELINE_PER_QUAL, q14_post * PIPELINE_PER_QUAL)

    # --- Slide 5: money at stake ---------------------------------------------------------------
    mob_q_pre = total(c, "qualified_within_14_days", PRE, device="Mobile") / 4
    mob_q_post = total(c, "qualified_within_14_days", MATURE_POST, device="Mobile") / 2
    desk_q_pre = total(c, "qualified_within_14_days", PRE, device="Desktop") / 4
    desk_q_post = total(c, "qualified_within_14_days", MATURE_POST, device="Desktop") / 2
    N["observed_pipeline_change_wk"] = {
        "total": (q14_post - q14_pre) * PIPELINE_PER_QUAL,
        "mobile": (mob_q_post - mob_q_pre) * PIPELINE_PER_QUAL,
        "desktop": (desk_q_post - desk_q_pre) * PIPELINE_PER_QUAL,
    }

    # Estimate A: mobile starts since v2 x June completion rate (by channel), minus actual requests
    lost_leads, lost_q = 0.0, 0.0
    for ch in CHANNELS:
        comp_pre = (total(f, "form_submissions", PRE, device="Mobile", channel=ch)
                    / total(f, "form_starts", PRE, device="Mobile", channel=ch))
        expected = total(f, "form_starts", POST, device="Mobile", channel=ch) * comp_pre
        lost = expected - total(f, "form_submissions", POST, device="Mobile", channel=ch)
        lost_leads += lost
        lost_q += lost * rates[(ch, "Mobile")]
    N["estA_lost_mobile_requests_4wk"] = lost_leads
    N["estA_lost_qualified_4wk"] = lost_q
    N["estA_pipeline_4wk"] = lost_q * PIPELINE_PER_QUAL

    # Estimate B: mobile qualified vs June run-rate, with 13/20 Jul projected at mature rates
    mob_post_q = total(c, "qualified_within_14_days", MATURE_POST, device="Mobile")
    for w in IMMATURE:
        cw = c[(c.week_start == w) & (c.device == "Mobile")]
        mob_post_q += sum(r.new_leads * rates[(r.channel, r.device)] for r in cw.itertuples())
    N["estB_lost_qualified_4wk"] = mob_q_pre * 4 - mob_post_q
    N["estB_pipeline_4wk"] = N["estB_lost_qualified_4wk"] * PIPELINE_PER_QUAL

    N["per_extra_week_range"] = (-N["observed_pipeline_change_wk"]["mobile"], N["estA_pipeline_4wk"] / 4)
    N["acv_scenario_4wk"] = (N["estB_pipeline_4wk"] * WIN_RATE_SCENARIO, N["estA_pipeline_4wk"] * WIN_RATE_SCENARIO)

    # Paid search economics (14-day qualified, so June vs mature post only)
    def paid_stats(weeks, dev=None):
        flt = {"device": dev} if dev else {}
        spend = total(p, "spend_usd", weeks, **flt)
        leads = total(c, "new_leads", weeks, channel="Paid Search", **flt)
        q = total(c, "qualified_within_14_days", weeks, channel="Paid Search", **flt)
        return {"spend": spend, "leads": int(leads), "q14": int(q), "cpl": spend / leads,
                "cpql": spend / q, "pipeline_per_dollar": q * PIPELINE_PER_QUAL / spend}

    N["paid_june"] = paid_stats(PRE)
    N["paid_mature_post"] = paid_stats(MATURE_POST)
    N["paid_mobile"] = (paid_stats(PRE, "Mobile"), paid_stats(MATURE_POST, "Mobile"))
    N["paid_desktop"] = (paid_stats(PRE, "Desktop"), paid_stats(MATURE_POST, "Desktop"))
    N["paid_spend_month"] = total(p, "spend_usd", PRE) / 4 * WEEKS_PER_MONTH

    org_q_wk = total(c, "qualified_within_14_days", PRE, channel="Organic Search") / 4
    N["organic_pipeline_month_june"] = org_q_wk * PIPELINE_PER_QUAL * WEEKS_PER_MONTH
    N["share_of_june_qualified"] = {
        ch: total(c, "qualified_within_14_days", PRE, channel=ch) / total(c, "qualified_within_14_days", PRE)
        for ch in CHANNELS
    }
    N["paid_alt_pipeline_month"] = SEO_RETAINER_MONTH * N["paid_june"]["pipeline_per_dollar"]
    N["breakeven_share_of_organic"] = N["paid_alt_pipeline_month"] / N["organic_pipeline_month_june"]
    nb_share = total(s, "clicks", PRE, query_segment="Non-brand") / total(s, "clicks", PRE)
    N["nonbrand_click_share_june"] = nb_share
    N["breakeven_share_of_nonbrand"] = N["breakeven_share_of_organic"] / nb_share

    # Search health
    for seg in ["Brand", "Non-brand"]:
        k = "brand" if seg == "Brand" else "nonbrand"
        N[f"{k}_clicks_wk"] = (per_week(s, "clicks", PRE, query_segment=seg),
                               per_week(s, "clicks", POST, query_segment=seg))
        N[f"{k}_impr_wk"] = (per_week(s, "impressions", PRE, query_segment=seg),
                             per_week(s, "impressions", POST, query_segment=seg))
        N[f"{k}_positions"] = sorted(s[s.query_segment == seg].avg_position.unique().tolist())
        N[f"{k}_ctr"] = sorted((s[s.query_segment == seg].clicks / s[s.query_segment == seg].impressions)
                               .round(4).unique().tolist())
    return N


def show(N):
    m = lambda v: f"${v/1e6:.2f}M" if abs(v) >= 1e6 else f"${v/1e3:,.0f}k"
    print("\nSLIDE 2  where the drop is (weekly averages, June -> since v2)")
    print(f"  Sessions            {N['sessions_wk'][0]:,.0f} -> {N['sessions_wk'][1]:,.0f}  ({pct(N['sessions_wk'][1], N['sessions_wk'][0]):+.1%})")
    print(f"  Search clicks       {N['search_clicks_wk'][0]:,.0f} -> {N['search_clicks_wk'][1]:,.0f}  ({pct(N['search_clicks_wk'][1], N['search_clicks_wk'][0]):+.1%})")
    print(f"  Mobile form starts  {N['mobile_starts_wk'][0]:.1f} -> {N['mobile_starts_wk'][1]:.1f}  ({pct(N['mobile_starts_wk'][1], N['mobile_starts_wk'][0]):+.1%})")
    print(f"  Mobile completion   {N['mobile_completion'][0]:.1%} -> {N['mobile_completion'][1]:.1%}")
    print(f"  Desktop completion  {N['desktop_completion'][0]:.1%} -> {N['desktop_completion'][1]:.1%}")
    print(f"  Mobile requests     {N['mobile_requests_wk'][0]:.2f} -> {N['mobile_requests_wk'][1]:.2f}  ({pct(N['mobile_requests_wk'][1], N['mobile_requests_wk'][0]):+.1%})")
    print(f"  Desktop requests    {N['desktop_requests_wk'][0]:.2f} -> {N['desktop_requests_wk'][1]:.2f}  ({pct(N['desktop_requests_wk'][1], N['desktop_requests_wk'][0]):+.1%})")
    print(f"  All requests        {N['all_requests_wk'][0]:.2f} -> {N['all_requests_wk'][1]:.2f}  ({pct(N['all_requests_wk'][1], N['all_requests_wk'][0]):+.1%})")
    for ch, (a, b) in N["mobile_requests_by_channel_wk"].items():
        print(f"    mobile {ch:<15} {a:6.2f} -> {b:6.2f}  ({pct(b, a):+.1%})")

    print("\nSLIDE 3  mechanism (share of form attempts)")
    for dev in ["mobile", "desktop"]:
        a, b = N[f"{dev}_phone_rej_rate"]
        o1, o2 = N[f"{dev}_other_abandon_rate"]
        print(f"  {dev:<8} phone rejections {a:.1%} -> {b:.1%} | other abandonment {o1:.1%} -> {o2:.1%}")
    print(f"  Mobile phone rejections per week {N['mobile_phone_rej_wk'][0]:.1f} -> {N['mobile_phone_rej_wk'][1]:.1f}"
          f"; total since v2 = {N['mobile_phone_rej_total_since_v2']}")
    print(f"  14-day qualification of mobile leads that got through: {N['mobile_qual_rate'][0]:.1%} -> {N['mobile_qual_rate'][1]:.1%}"
          f" (desktop {N['desktop_qual_rate'][0]:.1%} -> {N['desktop_qual_rate'][1]:.1%})")

    print("\nSLIDE 4  qualified leads by acquisition week")
    for w, r in N["weekly_cohorts"].items():
        extra = f"  projected at 14 days ~{r['q14_projected']:.1f}" if "q14_projected" in r else ""
        print(f"  {w}  leads {r['leads']:>3}  14-day {str(r['q14']):>4}  at snapshot {r['q_snapshot']:>3}"
              f"  dashboard pipeline {m(r['pipeline_snapshot'])}{extra}")
    print(f"  14-day qualified per week: June {N['q14_wk'][0]:.1f} -> mature post {N['q14_wk'][1]:.1f}"
          f" ({pct(N['q14_wk'][1], N['q14_wk'][0]):+.1%})")

    print("\nSLIDE 5  money")
    o = N["observed_pipeline_change_wk"]
    print(f"  OBSERVED pipeline per week {m(N['pipeline14_wk'][0])} -> {m(N['pipeline14_wk'][1])}:"
          f" total {m(o['total'])}, mobile {m(o['mobile'])}, desktop {m(o['desktop'])}")
    print(f"  ESTIMATE A lost mobile requests since v2 {N['estA_lost_mobile_requests_4wk']:.1f},"
          f" lost qualified {N['estA_lost_qualified_4wk']:.1f}, pipeline {m(N['estA_pipeline_4wk'])}")
    print(f"  ESTIMATE B lost mobile qualified {N['estB_lost_qualified_4wk']:.1f}, pipeline {m(N['estB_pipeline_4wk'])}")
    print(f"  Each further week: {m(N['per_extra_week_range'][0])} to {m(N['per_extra_week_range'][1])}")
    print(f"  SCENARIO ACV at {WIN_RATE_SCENARIO:.0%} win: {m(N['acv_scenario_4wk'][0])} to {m(N['acv_scenario_4wk'][1])} so far")
    pj, pm = N["paid_june"], N["paid_mature_post"]
    print(f"  Paid June: spend {m(pj['spend'])}, {pj['leads']} leads, {pj['q14']} qualified,"
          f" ${pj['cpl']:.0f}/lead, ${pj['cpql']:.0f}/qualified, {pj['pipeline_per_dollar']:.1f}x pipeline per $")
    print(f"  Paid mature post: ${pm['cpql']:.0f}/qualified, {pm['pipeline_per_dollar']:.1f}x")
    a, b = N["paid_mobile"]
    print(f"  Paid mobile cost per qualified lead ${a['cpql']:.0f} -> ${b['cpql']:.0f};"
          f" desktop ${N['paid_desktop'][0]['cpql']:.0f} -> ${N['paid_desktop'][1]['cpql']:.0f}")
    print(f"  Paid spend per month {m(N['paid_spend_month'])}; organic pipeline per month (June) {m(N['organic_pipeline_month_june'])}")
    print(f"  Share of June qualified: " + ", ".join(f"{k} {v:.0%}" for k, v in N["share_of_june_qualified"].items()))
    print(f"  $8k/month in paid at June average returns: {m(N['paid_alt_pipeline_month'])} pipeline")
    print(f"  Break-even: SEO must account for {N['breakeven_share_of_organic']:.1%} of organic pipeline"
          f" ({N['breakeven_share_of_nonbrand']:.1%} of the non-brand share; non-brand = {N['nonbrand_click_share_june']:.0%} of June clicks)")
    print(f"  Search: brand clicks {N['brand_clicks_wk'][0]:.0f} -> {N['brand_clicks_wk'][1]:.0f}, non-brand {N['nonbrand_clicks_wk'][0]:.0f}"
          f" -> {N['nonbrand_clicks_wk'][1]:.0f}; non-brand impressions {N['nonbrand_impr_wk'][0]:,.0f} -> {N['nonbrand_impr_wk'][1]:,.0f};"
          f" positions brand {N['brand_positions']} non-brand {N['nonbrand_positions']}; CTR {N['brand_ctr']} / {N['nonbrand_ctr']}")


if __name__ == "__main__":
    data = load(find_folder())
    ok = run_checks(data)
    show(compute(data))
    if not ok:
        sys.exit("\nAt least one reconciliation check failed. Fix before trusting the numbers.")
