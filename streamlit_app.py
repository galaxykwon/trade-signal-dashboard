"""
수출입 무역통계 기반 투자
Streamlit Cloud 배포용
"""

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import json

st.set_page_config(
    page_title="수출입 투자 시그널 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .metric-card {
    background: #f8f9fa; border-radius: 10px;
    padding: 16px; margin: 4px 0;
    border-left: 4px solid #dee2e6;
  }
  .signal-3 { border-left-color: #ff4b4b !important; background: #fff5f5; }
  .signal-2 { border-left-color: #21c354 !important; background: #f0fff4; }
  .signal-1 { border-left-color: #1c83e1 !important; background: #f0f7ff; }
  .signal-0 { border-left-color: #adb5bd !important; }
  .score-big { font-size: 2.2rem; font-weight: 700; line-height: 1; }
  div[data-testid="stMetric"] { background:#f8f9fa; border-radius:8px; padding:8px; }
</style>
""", unsafe_allow_html=True)

# ── 상수 ──────────────────────────────────────────────────────────
ALL_SECTORS = ["반도체","반도체장비","전력기기","석유화학","조선","로봇","화장품"]

# ── 사이드바 ──────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 설정")

    try:
        api_key = st.secrets["TRADE_API_KEY"]
        st.success("API 키 ✅ (secrets)")
    except Exception:
        api_key = st.text_input(
            "공공데이터포털 API 키",
            type="password",
            placeholder="data.go.kr에서 발급",
            help="관세청_품목별 수출입실적 + 시군구별 품목별 수출입실적 신청 필요"
        )

    lookback = st.slider("분석 기간 (개월)", 12, 36, 24)

    # Fix2: 디폴트를 모든 섹터로 변경
    tab_filter = st.multiselect(
        "표시할 섹터",
        ALL_SECTORS,
        default=ALL_SECTORS,   # ← 전체 선택
    )

    run_btn = st.button("🔄 분석 실행", type="primary", use_container_width=True)

    st.divider()
    st.caption("📌 관세청 수출입 무역통계")
    st.caption("📅 통계는 1~2개월 지연 공표")
    st.caption("⚠️ 투자 참고용, 권유 아님")
    st.caption("✨ by Galaxy")

# ══════════════════════════════════════════════════════════════════
#  Fix1: 캐시 — st.cache_data 는 Streamlit 서버 프로세스 레벨에서 유지
#         따라서 브라우저를 새로 열거나 앱이 재시작되면 사라짐
#         세션 내에서는 session_state["results"] 가 있으면 재실행 불필요
#         → run_btn 클릭 시 results 가 이미 있으면 즉시 반환
# ══════════════════════════════════════════════════════════════════

# ── 파일 캐시 경로 (Streamlit Cloud /tmp 는 세션 간 유지됨) ──────
import os, hashlib, pickle
from concurrent.futures import ThreadPoolExecutor, as_completed

_CACHE_DIR = "/tmp/trade_cache"
os.makedirs(_CACHE_DIR, exist_ok=True)

def _cache_path(api_key: str, lookback: int) -> str:
    h = hashlib.md5(f"{api_key}{lookback}".encode()).hexdigest()[:12]
    return os.path.join(_CACHE_DIR, f"results_{h}.pkl")

def _load_file_cache(api_key: str, lookback: int):
    """24시간 파일 캐시 로드. 없거나 만료되면 None 반환."""
    import time
    path = _cache_path(api_key, lookback)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < 86400:  # 24시간
            try:
                with open(path, "rb") as f:
                    return pickle.load(f)
            except Exception:
                pass
    return None

def _save_file_cache(api_key: str, lookback: int, results: dict):
    try:
        with open(_cache_path(api_key, lookback), "wb") as f:
            pickle.dump(results, f)
    except Exception:
        pass

def run_with_progress(api_key: str, lookback: int):
    from analyzer import (
        NATIONAL_SECTORS, COMPANY_SECTORS, REGIONS,
        fetch_national, fetch_region, pool, evaluate,
    )
    from dateutil.relativedelta import relativedelta

    today    = datetime.today()
    start_dt = today - relativedelta(months=lookback)
    sp, ep   = start_dt.strftime("%Y%m"), today.strftime("%Y%m")

    # 전체 호출 수 계산
    nat_hs = set()
    for cfg in NATIONAL_SECTORS.values():
        for rule in cfg["signal_rules"].values():
            nat_hs.update(rule["codes"])

    reg_calls = set()
    for cfg in COMPANY_SECTORS.values():
        rcodes = [REGIONS[r] for r in cfg["export_regions"] if r in REGIONS]
        for rule in cfg["export_rules"].values():
            for hs in rule["codes"]:
                for rc in rcodes:
                    reg_calls.add((hs, rc))
        if cfg["capex_rule"]:
            ccodes = [REGIONS[r] for r in cfg["capex_regions"] if r in REGIONS]
            for hs in cfg["capex_rule"]["codes"]:
                for rc in ccodes:
                    reg_calls.add((hs, rc))

    total = len(nat_hs) + len(reg_calls)

    # ── 진행 UI ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 📡 데이터 수집 중... 잠시 기다려 주세요")
    st.caption("⏳ 매일 첫 실행 시 5~10분 소요 · 이후 24시간 캐시 (즉시 로드)")
    prog      = st.progress(0, text="준비 중...")
    cur_task  = st.empty()
    cur_detail = st.empty()
    st.markdown("**완료된 항목**")
    done_area = st.empty()
    st.markdown("---")

    done: dict = {}
    step = [0]

    def _upd(msg: str, detail: str = ""):
        step[0] += 1
        pct = min(int(step[0] / total * 100), 99)
        prog.progress(pct, text=f"{pct}% — {msg}")
        cur_task.info(f"🔄 **{msg}**")
        if detail:
            cur_detail.caption(f"└ {detail}")
        if done:
            done_area.markdown("  ".join(f"✅ `{k}`" for k in done))

    results = {
        "national": {}, "company": {},
        "meta": {"sp": sp, "ep": ep, "generated": datetime.now().isoformat()}
    }

    # ── 병렬 수집: 모든 HS코드를 동시에 호출 (3~4배 빠름) ──────
    _upd("전체 HS코드 병렬 수집 시작", f"총 {len(nat_hs)}개 전국 + {len(reg_calls)}개 지역 코드")

    nat_cache: dict = {}
    reg_cache: dict = {}

    # 전국 코드 병렬 수집
    def _fetch_nat(hs):
        return hs, fetch_national(api_key, hs, sp, ep)

    def _fetch_reg(args):
        hs, rc = args
        return (hs, rc), fetch_region(api_key, hs, rc, sp, ep)

    nat_done_count = [0]
    reg_done_count = [0]

    with ThreadPoolExecutor(max_workers=5) as ex:
        # 전국 먼저
        nat_futs = {ex.submit(_fetch_nat, hs): hs for hs in nat_hs}
        for fut in as_completed(nat_futs):
            hs, df = fut.result()
            nat_cache[hs] = df
            nat_done_count[0] += 1
            _upd(
                f"전국 수집 {nat_done_count[0]}/{len(nat_hs)} 완료",
                f"HS {hs}"
            )

    with ThreadPoolExecutor(max_workers=5) as ex:
        # 지역 코드
        reg_futs = {ex.submit(_fetch_reg, call): call for call in reg_calls}
        for fut in as_completed(reg_futs):
            key, df = fut.result()
            reg_cache[key] = df
            reg_done_count[0] += 1
            hs, rc = key
            rname = next((n for n, v in REGIONS.items() if v == rc), rc)
            _upd(
                f"지역 수집 {reg_done_count[0]}/{len(reg_calls)} 완료",
                f"HS {hs} @ {rname}"
            )

    # ── 시그널 평가 (빠름) ──────────────────────────────────────
    _upd("시그널 평가 중...", "")

    for sector, cfg in NATIONAL_SECTORS.items():
        results["national"][sector] = {"icon": cfg["icon"], "signals": {}}
        for sig_name, rule in cfg["signal_rules"].items():
            dfs = [nat_cache[hs] for hs in rule["codes"]
                   if hs in nat_cache and not nat_cache[hs].empty]
            ev = evaluate(pool(dfs), rule["direction"], rule["threshold"])
            ev.update({"description": rule["desc"],
                       "direction": rule["direction"],
                       "stocks": rule["stocks"]})
            results["national"][sector]["signals"][sig_name] = ev
        done[sector] = True

    for company, cfg in COMPANY_SECTORS.items():
        label = f"{cfg['corp']} {cfg['seg']}"
        results["company"][company] = {
            "corp": cfg["corp"], "seg": cfg["seg"], "color": cfg["color"],
            "export_signals": {}, "capex_signal": None,
        }
        ecodes = [REGIONS[r] for r in cfg["export_regions"] if r in REGIONS]
        for sig_name, rule in cfg["export_rules"].items():
            dfs = [reg_cache[(hs, rc)]
                   for hs in rule["codes"] for rc in ecodes
                   if (hs, rc) in reg_cache and not reg_cache[(hs, rc)].empty]
            ev = evaluate(pool(dfs), "수출", rule["threshold"])
            ev["description"] = rule["desc"]
            results["company"][company]["export_signals"][sig_name] = ev

        if cfg["capex_rule"]:
            rule   = cfg["capex_rule"]
            ccodes = [REGIONS[r] for r in cfg["capex_regions"] if r in REGIONS]
            dfs = [reg_cache[(hs, rc)]
                   for hs in rule["codes"] for rc in ccodes
                   if (hs, rc) in reg_cache and not reg_cache[(hs, rc)].empty]
            ev = evaluate(pool(dfs), "수입", rule["threshold"])
            ev.update({"description": rule["desc"], "rule_name": rule["name"]})
            results["company"][company]["capex_signal"] = ev

        done[label] = True

    # 파일 캐시 저장 (24시간 유지)
    _save_file_cache(api_key, lookback, results)

    prog.progress(100, text="✅ 완료!")
    cur_task.success("🎉 모든 데이터 수집 완료!")
    cur_detail.empty()
    done_area.markdown("  ".join(f"✅ `{k}`" for k in done))
    return results

# ── 메인 헤더 ────────────────────────────────────────────────────
st.title("📊 수출입 무역통계 기반 투자 분석")
st.caption("관세청 HS코드 × 지역코드 기반 — 삼성전자·SK하이닉스 + 7개 섹터 통합 분석")

if "results" not in st.session_state:
    st.session_state.results = None

# Fix1 핵심: run_btn 클릭 시 처리
# ── Fix: 버튼 클릭과 데이터 수집을 분리 ────────────────────────
# 버튼은 need_run 플래그만 세우고 rerun → 다음 사이클에서 진행창 먼저 그린 뒤 수집
if run_btn:
    if not api_key:
        st.error("API 키를 입력하세요.")
    else:
        prev = st.session_state.get("_last_run", {})
        same = (prev.get("api_key") == api_key[:8] and
                prev.get("lookback") == lookback and
                st.session_state.results is not None)
        if same:
            st.success("✅ 이미 분석된 결과입니다. 아래에서 확인하세요.")
            # need_run 없이 그냥 진행 (결과가 이미 있음)
        else:
            # 진행창을 먼저 그리기 위해 플래그만 세우고 rerun
            st.session_state["need_run"]  = True
            st.session_state["run_key"]   = api_key
            st.session_state["run_lb"]    = lookback
            st.session_state.results      = None
            st.rerun()

# ── need_run: 진행창 먼저 그린 뒤 수집 ─────────────────────────
if st.session_state.get("need_run"):
    _ak = st.session_state.get("run_key", "")
    _lb = st.session_state.get("run_lb", 24)
    if _ak:
        # 파일 캐시 먼저 확인 (24시간 유효)
        cached = _load_file_cache(_ak, _lb)
        if cached:
            st.session_state.results  = cached
            st.session_state["need_run"] = False
            st.session_state["_last_run"] = {"api_key": _ak[:8], "lookback": _lb}
            st.success("✅ 캐시 로드 완료 (즉시)")
            st.rerun()
        else:
            try:
                results = run_with_progress(_ak, _lb)
                st.session_state.results  = results
                st.session_state["need_run"] = False
                st.session_state["_last_run"] = {"api_key": _ak[:8], "lookback": _lb}
                st.rerun()
            except Exception as e:
                st.session_state["need_run"] = False
                st.error(f"오류: {e}")
    st.stop()

# ── 결과 없으면 안내 ─────────────────────────────────────────────
if not st.session_state.results:
    st.info("👈 사이드바에서 API 키 입력 후 **분석 실행** 버튼을 누르세요.")
    with st.expander("📋 분석 대상 HS코드 목록"):
        rows = [
            ["반도체","8542321010","DRAM 수출"],
            ["반도체","8542323000","HBM·MCP 수출"],
            ["반도체","8542321030","NAND 수출"],
            ["전력기기","8504230000","초고압 변압기 수출"],
            ["전력기기","8507802000","ESS 배터리 수출"],
            ["조선","8901200000","탱커·LNG선 수출"],
            ["로봇","8479501000","산업용 로봇 수출"],
            ["삼성전자","8542323000 @ 화성·평택","HBM 수출 (지역필터)"],
            ["SK하이닉스","8542323000 @ 이천·청주","HBM 수출 (지역필터)"],
        ]
        st.dataframe(pd.DataFrame(rows, columns=["섹터","HS코드","설명"]),
                     use_container_width=True, hide_index=True)
    st.stop()

results = st.session_state.results
nat  = results["national"]
com  = results["company"]
meta = results["meta"]

# ── 공용 함수 ─────────────────────────────────────────────────────
from analyzer import sector_score, NATIONAL_SECTORS

def sig_color(s: int) -> str:
    return {3:"#ff4b4b",2:"#21c354",1:"#1c83e1",0:"#adb5bd"}.get(s,"#adb5bd")

def sig_emoji(s: str) -> str:
    if "강한" in s: return "🔥"
    if "매수" in s: return "✅"
    if "주시" in s: return "👀"
    return "⚪"

def corp_score(data: dict) -> int:
    sigs = list(data["export_signals"].values())
    if data["capex_signal"]: sigs.append(data["capex_signal"])
    if not sigs: return 0
    return round(sum(s.get("strength",0) for s in sigs) / (len(sigs)*3) * 100)

# ════════════════════════════════════════════════════════
#  탭 구성
# ════════════════════════════════════════════════════════
tab_overview, tab_sector, tab_company, tab_chart = st.tabs([
    "🏆 종합 순위", "🌏 섹터 분석", "🏢 핵심종목", "📈 차트",
])

# ══════════════ 탭1: 종합 순위 ══════════════
with tab_overview:
    st.subheader("📊 투자 매력도 종합 순위")
    st.caption(f"분석 기간: {meta['sp']} ~ {meta['ep']}")

    filtered_nat = {k:v for k,v in nat.items() if not tab_filter or k in tab_filter}
    nat_scores   = {s: sector_score(d["signals"]) for s,d in filtered_nat.items()}
    com_scores   = {c: corp_score(d) for c,d in com.items()}

    col_r, col_t = st.columns(2)
    with col_r:
        if nat_scores:
            names  = list(nat_scores.keys())
            values = list(nat_scores.values())
            fig = go.Figure(go.Scatterpolar(
                r=values+[values[0]], theta=names+[names[0]],
                fill="toself",
                fillcolor="rgba(28,131,225,0.2)",
                line=dict(color="rgb(28,131,225)", width=2),
            ))
            fig.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0,100])),
                showlegend=False, height=320, margin=dict(t=30,b=10),
                title="섹터 투자 매력도",
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_t:
        rows = (
            [{"구분":"[섹터]",   "항목":s, "점수":v} for s,v in nat_scores.items()] +
            [{"구분":"[핵심종목]","항목":c.replace("_"," "),"점수":v} for c,v in com_scores.items()]
        )
        df_r = pd.DataFrame(rows).sort_values("점수", ascending=False)
        df_r["바"] = df_r["점수"].apply(lambda x: "█"*(x//10)+"░"*(10-x//10))
        st.dataframe(df_r[["구분","항목","점수","바"]],
                     use_container_width=True, hide_index=True, height=310)

    st.divider()
    st.subheader("섹터 스코어카드")
    cols = st.columns(min(len(filtered_nat), 4))
    for i,(sector,data) in enumerate(filtered_nat.items()):
        sc   = nat_scores.get(sector, 0)
        sigs = data["signals"]
        best = max(sigs.values(), key=lambda s: s.get("strength",0), default={})
        with cols[i%4]:
            c = sig_color(best.get("strength",0))
            st.markdown(f"""
            <div class="metric-card signal-{best.get('strength',0)}">
              <div style="font-size:1.1rem;font-weight:600">{data.get('icon','')} {sector}</div>
              <div class="score-big" style="color:{c}">{sc}점</div>
              <div style="font-size:0.85rem;margin-top:4px">{best.get('signal','N/A')}</div>
            </div>""", unsafe_allow_html=True)

    st.divider()
    st.subheader("핵심종목 스코어카드")
    ccols = st.columns(4)
    for i,(company,data) in enumerate(com.items()):
        sc   = com_scores[company]
        exp  = list(data["export_signals"].values())
        best = max(exp, key=lambda s: s.get("strength",0), default={})
        cap  = data.get("capex_signal") or {}
        with ccols[i%4]:
            color = data["color"]
            st.markdown(f"""
            <div class="metric-card signal-{best.get('strength',0)}">
              <div style="font-size:1rem;font-weight:700;color:{color}">{data['corp']}</div>
              <div style="font-size:0.8rem;color:#666">{data['seg']}</div>
              <div class="score-big" style="color:{sig_color(best.get('strength',0))}">{sc}점</div>
              <div style="font-size:0.8rem;margin-top:2px">수출: {best.get('signal','N/A')}</div>
              <div style="font-size:0.8rem">CAPEX: {cap.get('signal','N/A')}</div>
            </div>""", unsafe_allow_html=True)

# ══════════════ 탭2: 섹터 분석 ══════════════
with tab_sector:
    st.subheader("🌏 국가 섹터별 시그널 상세")
    filtered_nat = {k:v for k,v in nat.items() if not tab_filter or k in tab_filter}
    sel = st.selectbox("섹터 선택", list(filtered_nat.keys()))
    sec = filtered_nat[sel]

    for sig_name, sig in sec["signals"].items():
        st_val = sig.get("strength",0)
        with st.expander(
            f"{sig_emoji(sig['signal'])}  {sig_name}  —  {sig['signal']}  ({sig.get('trend','')})",
            expanded=(st_val >= 2)
        ):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("최신 YoY",  f"{sig.get('latest_yoy','N/A')}%",
                      delta=f"3M평균 {sig.get('avg3_yoy','N/A')}%")
            c2.metric("전월 YoY",  f"{sig.get('prev_yoy','N/A')}%")
            c3.metric("3달전 YoY", f"{sig.get('t3ago_yoy','N/A')}%")
            c4.metric("완료월",    sig.get("latest_period","N/A"))

            hist = sig.get("yoy_history",[])
            if hist:
                df_h = pd.DataFrame(hist, columns=["기간","YoY(%)"])
                fig  = go.Figure()
                fig.add_bar(
                    x=df_h["기간"], y=df_h["YoY(%)"],
                    marker_color=[
                        sig_color(3) if v>=20 else sig_color(2) if v>=10
                        else sig_color(1) if v>=0 else "#adb5bd"
                        for v in df_h["YoY(%)"]
                    ]
                )
                fig.add_hline(y=0, line_dash="dot", line_color="gray")
                fig.update_layout(height=220, margin=dict(t=10,b=10,l=0,r=0),
                                  xaxis_title=None, yaxis_title="YoY (%)")
                st.plotly_chart(fig, use_container_width=True)

            st.markdown(f"**해설:** {sig.get('description','')}")
            stocks = sig.get("stocks",[])
            if stocks:
                st.markdown("**관련주:** " + "  ".join(f"`{s}`" for s in stocks))

# ══════════════ 탭3: 핵심종목 ══════════════
with tab_company:
    st.subheader("🏢 삼성전자 · SK하이닉스 전용 분석")
    st.caption("지역코드 × HS코드 조합 — 기업별 제품군 정밀 추적")

    comp_sel  = st.selectbox("종목·구분 선택",
                             [f"{d['corp']} {d['seg']}" for d in com.values()])
    comp_key  = next(k for k,v in com.items() if f"{v['corp']} {v['seg']}" == comp_sel)
    comp_data = com[comp_key]
    color     = comp_data["color"]
    score     = corp_score(comp_data)

    h1,h2 = st.columns([3,1])
    with h1:
        st.markdown(f"### <span style='color:{color}'>{comp_data['corp']}</span> — {comp_data['seg']}",
                    unsafe_allow_html=True)
    with h2:
        st.metric("종합 점수", f"{score}점")

    st.markdown("#### 완제품 수출 시그널")
    exp_cols = st.columns(len(comp_data["export_signals"]))
    for i,(sname,sig) in enumerate(comp_data["export_signals"].items()):
        with exp_cols[i]:
            st_val = sig.get("strength",0)
            bg     = {"3":"#fff5f5","2":"#f0fff4","1":"#f0f7ff","0":"#f8f9fa"}.get(str(st_val),"#f8f9fa")
            border = sig_color(st_val)
            st.markdown(f"""
            <div style="background:{bg};border-left:4px solid {border};
                        border-radius:8px;padding:12px;margin:4px 0">
              <div style="font-size:0.85rem;font-weight:600">{sname}</div>
              <div style="font-size:1.3rem;margin:4px 0">{sig.get('signal','N/A')}</div>
              <div style="font-size:0.8rem">YoY: <b>{sig.get('latest_yoy','N/A')}%</b>  {sig.get('trend','')}</div>
              <div style="font-size:0.75rem;color:#666;margin-top:4px">{sig.get('description','')}</div>
            </div>""", unsafe_allow_html=True)

    if comp_data["capex_signal"]:
        st.markdown("#### 공급망 CAPEX 수입 시그널 (6~12개월 선행)")
        cap    = comp_data["capex_signal"]
        st_val = cap.get("strength",0)
        c1,c2  = st.columns([2,1])
        with c1:
            bg     = {"3":"#fff5f5","2":"#f0fff4","1":"#f0f7ff","0":"#f8f9fa"}.get(str(st_val),"#f8f9fa")
            border = sig_color(st_val)
            st.markdown(f"""
            <div style="background:{bg};border-left:4px solid {border};border-radius:8px;padding:16px">
              <div style="font-size:1rem;font-weight:600">{cap.get('rule_name','CAPEX')}</div>
              <div style="font-size:1.4rem;margin:8px 0">{cap.get('signal','N/A')}</div>
              <div>YoY: <b>{cap.get('latest_yoy','N/A')}%</b> &nbsp; {cap.get('trend','')}</div>
              <div style="font-size:0.85rem;color:#555;margin-top:8px">{cap.get('description','')}</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.metric("3개월 평균 YoY", f"{cap.get('avg3_yoy','N/A')}%")
            st.metric("완료월", cap.get("latest_period","N/A"))

    first_sig = next(iter(comp_data["export_signals"].values()), None)
    if first_sig and first_sig.get("yoy_history"):
        st.markdown("#### YoY 추이")
        df_h = pd.DataFrame(first_sig["yoy_history"], columns=["기간","YoY(%)"])
        fig  = go.Figure()
        fig.add_bar(
            x=df_h["기간"], y=df_h["YoY(%)"],
            marker_color=[
                sig_color(3) if v>=20 else sig_color(2) if v>=10
                else sig_color(1) if v>=0 else "#adb5bd"
                for v in df_h["YoY(%)"]
            ], name="YoY"
        )
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                          yaxis_title="YoY (%)", xaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("💡 시그널 조합 해석 가이드"):
        guide = [
            ["수출 🔥 + CAPEX ✅","현재 출하↑, 미래 생산↑","적극 매수 검토"],
            ["수출 ✅ + CAPEX 🔥","현재 선방, 다음 사이클 대비","분할 매수 검토"],
            ["수출 ⚪ + CAPEX 🔥","다운사이클 바닥, CAPEX 시작","6~12개월 후 반전 대비"],
            ["수출 🔥 + CAPEX ⚪","피크아웃 주의","비중 축소 고려"],
        ]
        st.dataframe(pd.DataFrame(guide, columns=["시그널 조합","의미","투자 행동"]),
                     use_container_width=True, hide_index=True)
        st.caption("⚠️ 지역 필터는 완벽한 기업 구분이 아닙니다. 협력업체 물량 혼재 가능.")

# ══════════════ 탭4: 차트 ══════════════
with tab_chart:
    st.subheader("📈 수출입 YoY 추이 비교")

    all_sigs = {}
    filtered_nat = {k:v for k,v in nat.items() if not tab_filter or k in tab_filter}
    for sector,data in filtered_nat.items():
        for sname,sig in data["signals"].items():
            if sig.get("yoy_history"):
                all_sigs[f"[{sector}] {sname}"] = sig
    for company,data in com.items():
        for sname,sig in data["export_signals"].items():
            if sig.get("yoy_history"):
                all_sigs[f"[{data['corp']} {data['seg']}] {sname}"] = sig
        cap = data.get("capex_signal")
        if cap and cap.get("yoy_history"):
            all_sigs[f"[{data['corp']} {data['seg']}] {cap.get('rule_name','CAPEX')}"] = cap

    selected = st.multiselect(
        "비교할 시그널 선택 (최대 5개)",
        list(all_sigs.keys()),
        default=list(all_sigs.keys())[:3],
        max_selections=5,
    )

    if selected:
        fig    = go.Figure()
        colors = ["#1c83e1","#ff4b4b","#21c354","#ff9900","#9c36b5"]
        for i,key in enumerate(selected):
            sig  = all_sigs[key]
            df_h = pd.DataFrame(sig["yoy_history"], columns=["기간","YoY(%)"])
            fig.add_trace(go.Scatter(
                x=df_h["기간"], y=df_h["YoY(%)"], name=key,
                mode="lines+markers",
                line=dict(color=colors[i%len(colors)], width=2),
                marker=dict(size=5),
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
        fig.update_layout(
            height=420, xaxis_title=None, yaxis_title="YoY (%)",
            legend=dict(orientation="h", y=-0.25),
            hovermode="x unified", margin=dict(t=20,b=60,l=0,r=0),
        )
        st.plotly_chart(fig, use_container_width=True)

        rows = []
        for key in selected:
            sig = all_sigs[key]
            rows.append({
                "시그널":       key,
                "최신YoY(%)":   sig.get("latest_yoy","N/A"),
                "전월YoY(%)":   sig.get("prev_yoy","N/A"),
                "3M평균(%)":    sig.get("avg3_yoy","N/A"),
                "추세":         sig.get("trend","N/A"),
                "판정":         sig.get("signal","N/A"),
                "완료월":       sig.get("latest_period","N/A"),
            })
        st.markdown("**최신 수치 비교**")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("위에서 시그널을 선택하세요.")

# ── 푸터 ──────────────────────────────────────────────────────────
st.divider()
try:
    t = datetime.fromisoformat(meta.get("generated","")).strftime("%Y-%m-%d %H:%M")
    st.caption(f"마지막 분석: {t}  |  분석기간: {meta['sp']} ~ {meta['ep']}")
except Exception:
    pass
st.caption("데이터 출처: 관세청 수출입 무역통계 (공공데이터포털)  |  ⚠️ 투자 참고용, 매수·매도 권유 아님")
