"""Layer 1: the statistical engine. Produces every number. No LLM, no guesses.

Dixon-Coles (1997) goal model:
  log(home goals rate) = home_adv[div] + attack[home] + defence[away]
  log(away goals rate) = attack[away] + defence[home]
  (defence > 0 means the team concedes more than average)
plus the Dixon-Coles correction (rho) for 0-0, 1-0, 0-1 and 1-1, which plain Poisson gets wrong.

- Time decay: a match half_life_days old counts half as much as one played today.
- Leagues of one country are fitted together, so a promoted team brings its history with it.
- A shrinkage prior (prior_sd) pulls teams with little data toward the league average
  instead of letting a few results produce extreme ratings.
- Uncertainty: the curvature of the likelihood (Laplace approximation) gives a covariance for
  all ratings. Each prediction is re-computed for `draws` plausible rating sets, so a team with
  thin history gets a visibly wider range, not false confidence.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

OU_LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


@dataclass
class GroupFit:
    group: str
    asof: str                      # ratings use results strictly before this date
    teams: list[str]
    divisions: list[str]
    theta: np.ndarray
    cov: np.ndarray
    rho: float
    n_matches: int
    team_recent_matches: dict[str, int]
    team_division: dict[str, str]  # latest division each team played in
    converged: bool
    data_hash: str
    params: dict
    seconds: float
    tidx: dict = field(default_factory=dict)
    didx: dict = field(default_factory=dict)

    def __post_init__(self):
        self.tidx = {t: i for i, t in enumerate(self.teams)}
        self.didx = {d: i for i, d in enumerate(self.divisions)}

    @property
    def T(self):
        return len(self.teams)

    @property
    def D(self):
        return len(self.divisions)

    def summary(self) -> dict:
        T, D = self.T, self.D
        ma = self.theta[2 * T:2 * T + D]
        md = self.theta[2 * T + D:2 * T + 2 * D]
        ha = self.theta[2 * T + 2 * D:]
        return {
            "group": self.group, "asof": self.asof, "n_matches": self.n_matches, "n_teams": T,
            "rho": round(self.rho, 4), "converged": self.converged, "data_hash": self.data_hash,
            "seconds": round(self.seconds, 2),
            "league_base_goals": {d: round(float(np.exp(ma[i] + md[i])), 3) for i, d in enumerate(self.divisions)},
            "division_strength": {d: round(float(ma[i] - md[i]), 3) for i, d in enumerate(self.divisions)},
            "home_advantage_x": {d: round(float(np.exp(ha[i])), 3) for i, d in enumerate(self.divisions)},
        }


def data_hash(df: pd.DataFrame) -> str:
    core = df[["div", "date", "home", "away", "hg", "ag"]].astype(str)
    return hashlib.sha1(core.to_csv(index=False).encode()).hexdigest()[:12]


def fit_group(df: pd.DataFrame, group: str, asof: pd.Timestamp, half_life_days: float,
              prior_sd: float, shots_weight: float = 0.0, warm: GroupFit | None = None) -> GroupFit:
    """Fit one league group on results played before `asof`.

    Every team in the group sits on one scale. Each team's attack and defence are pulled
    (by prior_sd) toward the average of the division it currently plays in, and those
    division averages are free parameters. Teams that moved between divisions tie the
    divisions together, so a promoted team is rated as a newcomer to the stronger league,
    not as the giant it was one level down.
    """
    t0 = time.time()
    d = df[df["date"] < asof]
    if len(d) < 30:
        raise ValueError(f"{group}: only {len(d)} results before {asof.date()}, too few to fit")
    teams = sorted(set(d["home"]) | set(d["away"]))
    divs = sorted(set(d["div"]))
    T, D = len(teams), len(divs)
    ti = {t: i for i, t in enumerate(teams)}
    di_map = {x: i for i, x in enumerate(divs)}
    last_div = {}
    for row in d[["div", "home", "away"]].itertuples(index=False):
        last_div[row.home] = row.div
        last_div[row.away] = row.div
    tdv = np.array([di_map[last_div[t]] for t in teams])
    hi = d["home"].map(ti).to_numpy()
    ai = d["away"].map(ti).to_numpy()
    di = d["div"].map(di_map).to_numpy()
    hg = d["hg"].to_numpy(dtype=float)
    ag = d["ag"].to_numpy(dtype=float)
    # Ratings target: actual goals, optionally blended with shots on target converted to goals
    # at the division's own rate. Shots are a steadier signal of quality than goals alone.
    yh, ya = hg.copy(), ag.copy()
    if shots_weight > 0 and "hst" in d:
        hst = d["hst"].to_numpy(dtype=float)
        ast = d["ast"].to_numpy(dtype=float)
        ok = np.isfinite(hst) & np.isfinite(ast)
        for k in range(len(divs)):
            m = ok & (di == k)
            sot = hst[m].sum() + ast[m].sum()
            if m.sum() >= 50 and sot > 0:
                conv = (hg[m].sum() + ag[m].sum()) / sot
                yh[m] = (1 - shots_weight) * hg[m] + shots_weight * conv * hst[m]
                ya[m] = (1 - shots_weight) * ag[m] + shots_weight * conv * ast[m]
    age = (asof - d["date"]).dt.days.to_numpy(dtype=float)
    w = 0.5 ** (age / half_life_days)
    prec = 1.0 / prior_sd ** 2
    prec_m = 1.0 / 9.0          # very weak: only fixes the scale's origin
    P = 2 * T + 3 * D

    def unpack(th):
        return th[:T], th[T:2 * T], th[2 * T:2 * T + D], th[2 * T + D:2 * T + 2 * D], th[2 * T + 2 * D:]

    def f(th):
        a, dfn, ma, md, ha = unpack(th)
        eh = ha[di] + a[hi] + dfn[ai]
        ea = a[ai] + dfn[hi]
        lh, la = np.exp(eh), np.exp(ea)
        ra, rd = a - ma[tdv], dfn - md[tdv]
        nll = (np.sum(w * (lh - yh * eh)) + np.sum(w * (la - ya * ea))
               + 0.5 * prec * (ra @ ra + rd @ rd) + 0.5 * prec_m * (ma @ ma + md @ md))
        gh, ga = w * (lh - yh), w * (la - ya)
        g = np.empty(P)
        g[:T] = np.bincount(hi, gh, T) + np.bincount(ai, ga, T) + prec * ra
        g[T:2 * T] = np.bincount(ai, gh, T) + np.bincount(hi, ga, T) + prec * rd
        g[2 * T:2 * T + D] = -prec * np.bincount(tdv, ra, D) + prec_m * ma
        g[2 * T + D:2 * T + 2 * D] = -prec * np.bincount(tdv, rd, D) + prec_m * md
        g[2 * T + 2 * D:] = np.bincount(di, gh, D)
        return nll, g

    x0 = np.zeros(P)
    base = np.log(max((yh @ w + ya @ w) / (2 * w.sum()), 0.2))
    x0[2 * T:2 * T + 2 * D] = base / 2
    x0[:2 * T] = base / 2
    x0[2 * T + 2 * D:] = 0.2
    if warm is not None:  # start from last week's ratings (backtest speed-up)
        for t, i in ti.items():
            j = warm.tidx.get(t)
            if j is not None:
                x0[i] = warm.theta[j]
                x0[T + i] = warm.theta[warm.T + j]
        for x, i in di_map.items():
            j = warm.didx.get(x)
            if j is not None:
                for blk in range(3):
                    x0[2 * T + blk * D + i] = warm.theta[2 * warm.T + blk * warm.D + j]
    res = minimize(f, x0, jac=True, method="L-BFGS-B", options={"maxiter": 3000, "gtol": 1e-7})
    th = res.x
    a, dfn, ma, md, ha = unpack(th)
    lh = np.exp(ha[di] + a[hi] + dfn[ai])
    la = np.exp(a[ai] + dfn[hi])

    # Laplace approximation: covariance = inverse of the Hessian at the optimum.
    n = len(d)
    Xh = sparse.csr_matrix((np.ones(3 * n), (np.repeat(np.arange(n), 3),
                            np.stack([hi, T + ai, 2 * T + 2 * D + di], axis=1).ravel())), shape=(n, P))
    Xa = sparse.csr_matrix((np.ones(2 * n), (np.repeat(np.arange(n), 2),
                            np.stack([ai, T + hi], axis=1).ravel())), shape=(n, P))
    H = (Xh.T @ sparse.diags(w * lh) @ Xh + Xa.T @ sparse.diags(w * la) @ Xa).toarray()
    R = np.zeros((T, D))
    R[np.arange(T), tdv] = 1.0
    nk = R.sum(axis=0)
    for off_t, off_m in ((0, 2 * T), (T, 2 * T + D)):
        H[off_t:off_t + T, off_t:off_t + T] += prec * np.eye(T)
        H[off_t:off_t + T, off_m:off_m + D] -= prec * R
        H[off_m:off_m + D, off_t:off_t + T] -= prec * R.T
        H[off_m:off_m + D, off_m:off_m + D] += np.diag(prec * nk + prec_m)
    H[np.diag_indices(P)] += 1e-9
    cov = np.linalg.inv(H)
    cov = 0.5 * (cov + cov.T)

    rho = fit_rho(hg, ag, lh, la, w)
    recent = d[d["date"] >= asof - pd.Timedelta(days=400)]
    counts = pd.concat([recent["home"], recent["away"]]).value_counts().to_dict()
    return GroupFit(group=group, asof=str(asof.date()), teams=teams, divisions=divs, theta=th, cov=cov,
                    rho=rho, n_matches=n, team_recent_matches={t: int(counts.get(t, 0)) for t in teams},
                    team_division=last_div, converged=bool(res.success), data_hash=data_hash(d),
                    params={"half_life_days": half_life_days, "prior_sd": prior_sd,
                            "shots_weight": shots_weight},
                    seconds=time.time() - t0)


def fit_rho(hg, ag, lh, la, w) -> float:
    m = (hg <= 1) & (ag <= 1)
    x, y, l, u, ww = hg[m], ag[m], lh[m], la[m], w[m]

    def nll(r):
        tau = np.where((x == 0) & (y == 0), 1 - l * u * r,
              np.where((x == 0) & (y == 1), 1 + l * r,
              np.where((x == 1) & (y == 0), 1 + u * r, 1 - r)))
        return -np.sum(ww * np.log(np.clip(tau, 1e-9, None)))

    return float(minimize_scalar(nll, bounds=(-0.25, 0.25), method="bounded").x)


# ------------------------------------------------------------ prediction

def score_matrices(lh: np.ndarray, la: np.ndarray, rho: float, G: int) -> np.ndarray:
    """(S,) home rates, (S,) away rates -> (S, G+1, G+1) scoreline probabilities."""
    k = np.arange(G + 1)
    ph = poisson.pmf(k[None, :], lh[:, None])
    pa = poisson.pmf(k[None, :], la[:, None])
    M = ph[:, :, None] * pa[:, None, :]
    M[:, 0, 0] *= 1 - lh * la * rho
    M[:, 0, 1] *= 1 + lh * rho
    M[:, 1, 0] *= 1 + la * rho
    M[:, 1, 1] *= 1 - rho
    M = np.clip(M, 0, None)
    return M / M.sum(axis=(1, 2), keepdims=True)


def markets_from(M: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Scoreline matrices -> market probabilities (each an (S,) array) and total-goals distribution."""
    S, G1, _ = M.shape
    i = np.arange(G1)
    tot = i[:, None] + i[None, :]
    total = np.stack([M[:, tot == k].sum(axis=1) for k in range(2 * G1 - 1)], axis=1)
    cdf = np.cumsum(total, axis=1)
    out = {}
    for L in OU_LINES:
        under = cdf[:, int(L)]
        out[market_key("ou", L, "under")] = under
        out[market_key("ou", L, "over")] = 1 - under
    out[market_key("1x2", None, "home")] = M[:, i[:, None] > i[None, :]].sum(axis=1)
    out[market_key("1x2", None, "draw")] = M[:, i[:, None] == i[None, :]].sum(axis=1)
    out[market_key("1x2", None, "away")] = M[:, i[:, None] < i[None, :]].sum(axis=1)
    btts = 1 - M[:, 0, :].sum(axis=1) - M[:, :, 0].sum(axis=1) + M[:, 0, 0]
    out[market_key("btts", None, "yes")] = btts
    out[market_key("btts", None, "no")] = 1 - btts
    return out, total


def market_key(market: str, line, selection: str) -> str:
    return f"{market}_{line}_{selection}" if line is not None else f"{market}_{selection}"


@dataclass
class Prediction:
    lam_home: float
    lam_away: float
    lam_home_range: tuple
    lam_away_range: tuple
    total_goals: list[float]          # P(0), P(1), ... P(5), P(6+)
    markets: dict[str, float]         # point estimate
    intervals: dict[str, tuple]       # 10th-90th percentile across plausible ratings
    samples: dict[str, np.ndarray]    # per-draw probabilities (used for confidence)

    def public(self) -> dict:
        return {
            "xg_home": round(self.lam_home, 3), "xg_away": round(self.lam_away, 3),
            "xg_home_range": [round(x, 3) for x in self.lam_home_range],
            "xg_away_range": [round(x, 3) for x in self.lam_away_range],
            "total_goals": [round(x, 4) for x in self.total_goals],
            "markets": {k: {"p": round(v, 4), "p10": round(self.intervals[k][0], 4),
                            "p90": round(self.intervals[k][1], 4)} for k, v in self.markets.items()},
        }


def predict(fit: GroupFit, div: str, home: str, away: str, draws: int, max_goals: int,
            seed: int) -> Prediction:
    T, D = fit.T, fit.D
    h, a, dv = fit.tidx[home], fit.tidx[away], fit.didx[div]
    xh = np.array([h, T + a, 2 * T + 2 * D + dv])
    xa = np.array([a, T + h])
    eh, ea = fit.theta[xh].sum(), fit.theta[xa].sum()
    C = fit.cov
    var_h = C[np.ix_(xh, xh)].sum()
    var_a = C[np.ix_(xa, xa)].sum()
    cov_ha = C[np.ix_(xh, xa)].sum()
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal([eh, ea], [[var_h, cov_ha], [cov_ha, var_a]], size=draws)
    lh_s, la_s = np.exp(z[:, 0]), np.exp(z[:, 1])
    lh, la = float(np.exp(eh)), float(np.exp(ea))
    point, total = markets_from(score_matrices(np.array([lh]), np.array([la]), fit.rho, max_goals))
    samp, _ = markets_from(score_matrices(lh_s, la_s, fit.rho, max_goals))
    tg = total[0]
    total6 = list(tg[:6]) + [float(tg[6:].sum())]
    return Prediction(
        lam_home=lh, lam_away=la,
        lam_home_range=(float(np.percentile(lh_s, 10)), float(np.percentile(lh_s, 90))),
        lam_away_range=(float(np.percentile(la_s, 10)), float(np.percentile(la_s, 90))),
        total_goals=[float(x) for x in total6],
        markets={k: float(v[0]) for k, v in point.items()},
        intervals={k: (float(np.percentile(v, 10)), float(np.percentile(v, 90))) for k, v in samp.items()},
        samples=samp,
    )


def seed_for(fixture_id: str) -> int:
    return int(hashlib.sha1(fixture_id.encode()).hexdigest()[:8], 16)
