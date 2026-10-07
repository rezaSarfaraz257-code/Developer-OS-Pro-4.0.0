import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "./services/api";
import "./Referral.css";

export default function Referral({ go }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const load = () => apiFetch("/referrals/").then(async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "Unable to load referral program.");
    setData(d);
  }).catch((e) => setError(e.message));

  useEffect(() => { load(); }, []);

  const link = useMemo(() => data?.code ? window.location.origin + "/register?ref=" + encodeURIComponent(data.code) : "", [data?.code]);

  const copy = async () => {
    if (!link) return;
    await navigator.clipboard?.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const share = async () => {
    if (!link) return;
    if (navigator.share) {
      await navigator.share({
        title: "Developer OS",
        text: "Build faster with Developer OS. Join through my referral link.",
        url: link,
      }).catch(() => {});
    } else {
      await copy();
    }
  };

  if (!data && !error) return <div className="page loading">LOADING GROWTH PROGRAM...</div>;

  return <div className="page referral-page">
    <div className="hero-row">
      <div>
        <div className="eyebrow">GROWTH / REWARDS</div>
        <h1>Invite developers. Earn Pro.</h1>
        <p>Bring real developers into Developer OS. Every 10 qualified referrals unlocks 30 days of Pro.</p>
      </div>
      <button className="ghost" onClick={() => go("/")}>BACK TO COMMAND CENTER</button>
    </div>

    {error && <div className="error">{error}</div>}

    {data && <>
      <section className="referral-hero panel">
        <div>
          <span className="panel-kicker">YOUR REFERRAL LINK</span>
          <code className="referral-link">{link}</code>
          <div className="referral-actions">
            <button className="primary" onClick={copy}>{copied ? "COPIED ✓" : "COPY LINK"}</button>
            <button className="ghost" onClick={share}>SHARE</button>
          </div>
        </div>
        <div className="referral-progress">
          <span>{data.qualified} / {data.next_milestone}</span>
          <strong>{data.remaining} more</strong>
          <div className="referral-bar"><i style={{ width: Math.min(100, (data.qualified / Math.max(1, data.next_milestone)) * 100) + "%" }} /></div>
          <small>Next reward: {data.reward.duration_days} days of {data.reward.plan.toUpperCase()}</small>
        </div>
      </section>

      <div className="cards-grid referral-stats">
        <div className="project-card"><span>QUALIFIED</span><b>{data.qualified}</b><small>Verified + activated developers</small></div>
        <div className="project-card"><span>REMAINING</span><b>{data.remaining}</b><small>Until the next Pro reward</small></div>
        <div className="project-card"><span>REWARD</span><b>30 DAYS</b><small>Pro entitlement per milestone</small></div>
      </div>

      <section className="panel">
        <div className="panel-head"><div><span className="panel-kicker">REFERRAL LEDGER</span><h2>Your referrals</h2></div><span>{data.referrals.length}</span></div>
        {data.referrals.length ? data.referrals.map(r =>
          <div className="key-row" key={r.id}>
            <span><b>Developer referral</b></span>
            <small>{r.status.toUpperCase()} {r.qualified_at ? "· " + new Date(r.qualified_at).toLocaleDateString() : "· awaiting activation"}</small>
          </div>
        ) : <div className="empty">No referrals yet. Share your link to start.</div>}
      </section>

      <section className="panel">
        <div className="panel-head"><div><span className="panel-kicker">REWARD HISTORY</span><h2>Pro rewards</h2></div><span>{data.rewards.length}</span></div>
        {data.rewards.length ? data.rewards.map((r, i) =>
          <div className="key-row" key={String(r.milestone) + "-" + i}>
            <span><b>{r.milestone} referrals</b> · {r.duration_days} days Pro</span>
            <small>{new Date(r.starts_at).toLocaleDateString()} → {new Date(r.expires_at).toLocaleDateString()}</small>
          </div>
        ) : <div className="empty">Your first reward unlocks at 10 qualified referrals.</div>}
      </section>
    </>}
  </div>;
}
