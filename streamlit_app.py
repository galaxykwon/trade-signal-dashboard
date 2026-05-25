"""
수출입 무역통계 투자 시그널 대시보드
Streamlit Cloud 배포용
"""

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
from datetime import datetime
import json

# ── 페이지 설정 ───────────────────────────────────────────────────
st.set_page_config(
    page_title="수출입 투자 시그널 대시보드",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 스타일 ────────────────────────────────────────────────────────
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
  .tag { display:inline-block; font-size:11px; padding:2px 8px;
         border-radius:12px; margin:2px; background:#e9ecef; }
  div[data-testid="stMetric"] { background:#f8f9fa; border-radius:8px; padding:8px; }
</style>
""", unsafe_allow_html=True)

# ── 사이드바 ──────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 설정")

    # API 키: Streamlit secrets 우선, 없으면 입력 받기
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

    tab_filter = st.multiselect(
        "표시할 섹터",
        ["반도체","반도체장비","전력기기","석유화학","조선","로봇","화장품"],
        default=["반도체","전력기기","조선","로봇"],
    )

    run_btn = st.button("🔄 분석 실행", type="primary", use_container_width=True)

    st.divider()
    st.caption("📌 관세청 수출입 무역통계")
    st.caption("📅 통계는 1~2개월 지연 공표")
    st.caption("⚠️ 투자 참고용, 권유 아님")

# ── 진행 상태 표시용 run 함수 ────────────────────────────────────
@st.cache_data(ttl=86400, show_spinner=False)
def cached_run(api_key: str, lookback: int):
    from analyzer import run_all
    return run_all(api_key, lookback)

def run_with_progress(api_key: str, lookback: int):
    """
    섹터별 진행 상황을 실시간으로 표시하며 분석 실행
    캐시 히트 시 즉시 반환
    """
    import time as _time
    from analyzer import (
        NATIONAL_SECTORS, COMPANY_SECTORS, REGIONS,
        fetch_national, fetch_region, pool, evaluate,
        URL_NATIONAL, URL_REGION,
    )
    from datetime import datetime
    from dateutil.relativedelta import relativedelta

    today    = datetime.today()
    end_dt   = today
    start_dt = end_dt - relativedelta(months=lookback)
    sp, ep   = start_dt.strftime("%Y%m"), end_dt.strftime("%Y%m")

    # 전체 작업 수 계산
    nat_hs_codes = set()
    for cfg in NATIONAL_SECTORS.values():
        for rule in cfg["signal_rules"].values():
            nat_hs_codes.update(rule["codes"])

    reg_calls = []
    for cfg in COMPANY_SECTORS.values():
        reg_codes = [REGIONS[r] for r in cfg["export_regions"] if r in REGIONS]
        for rule in cfg["export_rules"].values():
            for hs in rule["codes"]:
                for rc in reg_codes:
                    reg_calls.append((hs, rc))
        if cfg["capex_rule"]:
            cap_codes = [REGIONS[r] for r in cfg["capex_regions"] if r in REGIONS]
            for hs in cfg["capex_rule"]["codes"]:
                for rc in cap_codes:
                    reg_calls.append((hs, rc))
    reg_calls = list(set(reg_calls))

    total_steps = len(nat_hs_codes) + len(reg_calls)

    # ── UI 요소 ──────────────────────────────────────────────────
    st.markdown("### 📡 데이터 수집 중...")
    progress_bar  = st.progress(0)
    status_text   = st.empty()
    detail_text   = st.empty()

    # 섹터별 완료 현황
    sector_status = st.empty()
    sector_done   = {}

    results = {
        "national": {}, "company": {},
        "meta": {"sp": sp, "ep": ep, "generated": datetime.now().isoformat()}
    }
    national_cache: dict = {}
    region_cache:   dict = {}
    step = 0

    def _update(msg: str, detail: str = ""):
        nonlocal step
        step += 1
        pct = min(int(step / total_steps * 100), 99)
        progress_bar.progress(pct)
        status_text.markdown(f"**{pct}%** — {msg}")
        if detail:
            detail_text.caption(detail)
        # 섹터 현황 표시
        done_list = " ".join(f"✅ {s}" for s in sector_done)
        if done_list:
            sector_status.caption(f"완료: {done_list}")

    # ── Part A: 전국 섹터 ─────────────────────────────────────────
    status_text.markdown("**Part A 시작** — 국가 섹터 데이터 수집")
    for sector, cfg in NATIONAL_SECTORS.items():
        results["national"][sector] = {"icon": cfg["icon"], "signals": {}}
        for sig_name, rule in cfg["signal_rules"].items():
            dfs = []
            for hs in rule["codes"]:
                if hs not in national_cache:
                    _update(
                        f"[{sector}] {sig_name}",
                        f"HS코드 {hs} 수집 중... ({sp}~{ep})"
                    )
                    national_cache[hs] = fetch_national(api_key, hs, sp, ep)
                if not national_cache[hs].empty:
                    dfs.append(national_cache[hs])
            ev = evaluate(pool(dfs), rule["direction"], rule["threshold"])
            ev["description"] = rule["desc"]
            ev["direction"]   = rule["direction"]
            ev["stocks"]      = rule["stocks"]
            results["national"][sector]["signals"][sig_name] = ev
        sector_done[sector] = True

    # ── Part B: 핵심종목 ─────────────────────────────────────────
    status_text.markdown("**Part B 시작** — 삼성전자·SK하이닉스 지역 데이터 수집")
    for company, cfg in COMPANY_SECTORS.items():
        label = f"{cfg['corp']} {cfg['seg']}"
        results["company"][company] = {
            "corp": cfg["corp"], "seg": cfg["seg"], "color": cfg["color"],
            "export_signals": {}, "capex_signal": None,
        }
        exp_codes = [REGIONS[r] for r in cfg["export_regions"] if r in REGIONS]
        for sig_name, rule in cfg["export_rules"].items():
            dfs = []
            for hs in rule["codes"]:
                for rc in exp_codes:
                    key = (hs, rc)
                    if key not in region_cache:
                        region_name = next(
                            (k for k, v in REGIONS.items() if v == rc), rc
                        )
                        _update(
                            f"[{label}] {sig_name}",
                            f"HS {hs} @ {region_name} 수집 중..."
                        )
                        region_cache[key] = fetch_region(api_key, hs, rc, sp, ep)
                    if not region_cache[key].empty:
                        dfs.append(region_cache[key])
            ev = evaluate(pool(dfs), "수출", rule["threshold"])
            ev["description"] = rule["desc"]
            results["company"][company]["export_signals"][sig_name] = ev

        if cfg["capex_rule"]:
            rule      = cfg["capex_rule"]
            cap_codes = [REGIONS[r] for r in cfg["capex_regions"] if r in REGIONS]
            dfs = []
            for hs in rule["codes"]:
                for rc in cap_codes:
                    key = (hs, rc)
                    if key not in region_cache:
                        region_name = next(
                            (k for k, v in REGIONS.items() if v == rc), rc
                        )
                        _update(
                            f"[{label}] CAPEX — {rule['name']}",
                            f"HS {hs} @ {region_name} 수집 중..."
                        )
                        region_cache[key] = fetch_region(api_key, hs, rc, sp, ep)
                    if not region_cache[key].empty:
                        dfs.append(region_cache[key])
            ev = evaluate(pool(dfs), "수입", rule["threshold"])
            ev["description"] = rule["desc"]
            ev["rule_name"]   = rule["name"]
            results["company"][company]["capex_signal"] = ev

        sector_done[label] = True

    # ── 완료 ─────────────────────────────────────────────────────
    progress_bar.progress(100)
    status_text.empty()
    detail_text.empty()
    sector_status.empty()

    return results

# ── 메인 헤더 ────────────────────────────────────────────────────
st.title("📊 수출입 무역통계 투자 시그널 대시보드")
st.caption("관세청 HS코드 × 지역코드 기반 — 삼성전자·SK하이닉스 + 7개 섹터 통합 분석")

# ── 실행 로직 ────────────────────────────────────────────────────
if "results" not in st.session_state:
    st.session_state.results = None

if run_btn:
    if not api_key:
        st.error("API 키를 입력하세요.")
    else:
        # 캐시 확인 먼저
        try:
            cached = cached_run(api_key, lookback)
            st.session_state.results = cached
            st.success(f"✅ 캐시 로드 완료! ({cached['meta']['sp']} ~ {cached['meta']['ep']})")
        except Exception:
            # 캐시 없으면 진행 표시하며 실행
            try:
                results = run_with_progress(api_key, lookback)
                # 결과를 캐시에도 저장
                st.session_state.results = results
                st.success(f"✅ 분석 완료! ({results['meta']['sp']} ~ {results['meta']['ep']})")
                st.rerun()
            except Exception as e:
                st.error(f"오류 발생: {e}")

# ── 결과 표시 ────────────────────────────────────────────────────
if not st.session_state.results:
    st.info("👈 사이드바에서 API 키 입력 후 **분석 실행** 버튼을 누르세요.")

    with st.expander("📋 분석 대상 HS코드 목록 보기"):
        rows = [
            ["반도체","8542321010","DRAM 수출"],
            ["반도체","8542323000","HBM·MCP 수출"],
            ["반도체","8542321030","NAND 수출"],
            ["전력기기","8504230000","초고압 변압기 수출"],
            ["전력기기","8507802000","ESS 배터리 수출"],
            ["조선","8901200000","탱커·LNG선 수출"],
            ["로봇","8479501000","산업용 로봇 수출"],
            ["삼성전자","8542323000 @ 화성·평택","HBM 수출 (지역 필터)"],
            ["SK하이닉스","8542323000 @ 이천·청주","HBM 수출 (지역 필터)"],
        ]
        st.dataframe(pd.DataFrame(rows, columns=["섹터","HS코드","설명"]),
                     use_container_width=True, hide_index=True)
    st.stop()

results = st.session_state.results
nat = results["national"]
com = results["company"]
meta = results["meta"]

# ════════════════════════════════════════════════════════
#  탭 구성
# ════════════════════════════════════════════════════════
tab_overview, tab_sector, tab_company, tab_chart = st.tabs([
    "🏆 종합 순위",
    "🌏 섹터 분석",
    "🏢 핵심종목",
    "📈 차트",
])

# ── 공용 함수 ─────────────────────────────────────────────────────
from analyzer import sector_score, NATIONAL_SECTORS

def sig_color(strength: int) -> str:
    return {3:"#ff4b4b", 2:"#21c354", 1:"#1c83e1", 0:"#adb5bd"}.get(strength,"#adb5bd")

def sig_emoji(s: str) -> str:
    if "강한" in s: return "🔥"
    if "매수" in s: return "✅"
    if "주시" in s: return "👀"
    return "⚪"

def company_total_score(data: dict) -> int:
    sigs = list(data["export_signals"].values())
    if data["capex_signal"]: sigs.append(data["capex_signal"])
    if not sigs: return 0
    return round(sum(s.get("strength",0) for s in sigs) / (len(sigs)*3) * 100)

# ════════════════════════════════════════════════════════
#  탭 1: 종합 순위
# ════════════════════════════════════════════════════════
with tab_overview:
    st.subheader("📊 투자 매력도 종합 순위")
    st.caption(f"분석 기간: {meta['sp']} ~ {meta['ep']}")

    # 섹터 점수
    nat_scores = {s: sector_score({**{k:v for k,v in d["signals"].items()}})
                  for s, d in nat.items() if s in tab_filter or not tab_filter}
    com_scores = {c: company_total_score(d) for c, d in com.items()}

    # 섹터 레이더 차트
    col_r, col_t = st.columns([1, 1])
    with col_r:
        if nat_scores:
            names  = list(nat_scores.keys())
            values = list(nat_scores.values())
            fig_radar = go.Figure(go.Scatterpolar(
                r=values + [values[0]],
                theta=names + [names[0]],
                fill="toself",
                fillcolor="rgba(28,131,225,0.2)",
                line=dict(color="rgb(28,131,225)", width=2),
            ))
            fig_radar.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0,100])),
                showlegend=False, height=320, margin=dict(t=30,b=10),
                title="섹터 투자 매력도",
            )
            st.plotly_chart(fig_radar, use_container_width=True)

    with col_t:
        # 전체 순위 테이블
        all_rows = (
            [{"구분":"[섹터]","항목":s,"점수":v} for s,v in nat_scores.items()] +
            [{"구분":"[핵심종목]","항목":c.replace("_"," "),"점수":v}
             for c,v in com_scores.items()]
        )
        df_rank = pd.DataFrame(all_rows).sort_values("점수", ascending=False)
        df_rank["바"] = df_rank["점수"].apply(lambda x: "█"*(x//10)+"░"*(10-x//10))

        st.dataframe(
            df_rank[["구분","항목","점수","바"]],
            use_container_width=True, hide_index=True, height=310,
        )

    st.divider()

    # 섹터별 스코어카드
    st.subheader("섹터 스코어카드")
    filtered_nat = {k:v for k,v in nat.items() if not tab_filter or k in tab_filter}
    cols = st.columns(min(len(filtered_nat), 4))
    for i, (sector, data) in enumerate(filtered_nat.items()):
        sc = nat_scores.get(sector, 0)
        icon = data.get("icon","")
        sigs = data["signals"]
        best_sig = max(sigs.values(), key=lambda s: s.get("strength",0), default={})
        with cols[i % 4]:
            color = sig_color(best_sig.get("strength",0))
            st.markdown(f"""
            <div class="metric-card signal-{best_sig.get('strength',0)}">
              <div style="font-size:1.1rem;font-weight:600">{icon} {sector}</div>
              <div class="score-big" style="color:{color}">{sc}점</div>
              <div style="font-size:0.85rem;margin-top:4px">{best_sig.get('signal','N/A')}</div>
            </div>
            """, unsafe_allow_html=True)

    st.divider()

    # 핵심종목 스코어카드
    st.subheader("핵심종목 스코어카드")
    corp_cols = st.columns(4)
    for i, (company, data) in enumerate(com.items()):
        sc = com_scores[company]
        exp_sigs = list(data["export_signals"].values())
        best = max(exp_sigs, key=lambda s: s.get("strength",0), default={})
        cap  = data.get("capex_signal") or {}
        with corp_cols[i % 4]:
            color = data["color"]
            st.markdown(f"""
            <div class="metric-card signal-{best.get('strength',0)}">
              <div style="font-size:1rem;font-weight:700;color:{color}">{data['corp']}</div>
              <div style="font-size:0.8rem;color:#666">{data['seg']}</div>
              <div class="score-big" style="color:{sig_color(best.get('strength',0))}">{sc}점</div>
              <div style="font-size:0.8rem;margin-top:2px">수출: {best.get('signal','N/A')}</div>
              <div style="font-size:0.8rem">CAPEX: {cap.get('signal','N/A')}</div>
            </div>
            """, unsafe_allow_html=True)

# ════════════════════════════════════════════════════════
#  탭 2: 섹터 분석
# ════════════════════════════════════════════════════════
with tab_sector:
    st.subheader("🌏 국가 섹터별 시그널 상세")

    filtered_nat = {k:v for k,v in nat.items() if not tab_filter or k in tab_filter}
    sector_sel = st.selectbox("섹터 선택", list(filtered_nat.keys()))
    sec_data   = filtered_nat[sector_sel]

    for sig_name, sig in sec_data["signals"].items():
        st_val  = sig.get("strength", 0)
        yoy_str = (f"YoY: [{sig.get('t3ago_yoy','?')}% → {sig.get('prev_yoy','?')}% → {sig['latest_yoy']}%]"
                   if sig.get("latest_yoy") is not None else "데이터 없음")

        with st.expander(
            f"{sig_emoji(sig['signal'])}  {sig_name}  —  {sig['signal']}  ({sig.get('trend','')})",
            expanded=(st_val >= 2)
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("최신 YoY", f"{sig.get('latest_yoy','N/A')}%",
                      delta=f"3M평균 {sig.get('avg3_yoy','N/A')}%")
            c2.metric("전월 YoY",  f"{sig.get('prev_yoy','N/A')}%")
            c3.metric("3달전 YoY", f"{sig.get('t3ago_yoy','N/A')}%")
            c4.metric("완료월", sig.get("latest_period","N/A"))

            # YoY 미니 차트
            hist = sig.get("yoy_history", [])
            if hist:
                df_h = pd.DataFrame(hist, columns=["기간","YoY(%)"])
                fig = go.Figure()
                fig.add_bar(x=df_h["기간"], y=df_h["YoY(%)"],
                            marker_color=[sig_color(3) if v>=20 else sig_color(2) if v>=10
                                          else sig_color(1) if v>=0 else "#adb5bd"
                                          for v in df_h["YoY(%)"]])
                fig.add_hline(y=0, line_dash="dot", line_color="gray")
                fig.update_layout(height=220, margin=dict(t=10,b=10,l=0,r=0),
                                  xaxis_title=None, yaxis_title="YoY (%)")
                st.plotly_chart(fig, use_container_width=True)

            st.markdown(f"**해설:** {sig.get('description','')}")
            stocks = sig.get("stocks", [])
            if stocks:
                st.markdown("**관련주:** " + "  ".join(f"`{s}`" for s in stocks))

# ════════════════════════════════════════════════════════
#  탭 3: 핵심종목
# ════════════════════════════════════════════════════════
with tab_company:
    st.subheader("🏢 삼성전자 · SK하이닉스 전용 분석")
    st.caption("지역코드 × HS코드 조합 — 기업별 제품군 정밀 추적")

    comp_sel = st.selectbox(
        "종목·구분 선택",
        [f"{d['corp']} {d['seg']}" for d in com.values()],
        format_func=lambda x: x,
    )
    comp_key  = next(k for k,v in com.items() if f"{v['corp']} {v['seg']}" == comp_sel)
    comp_data = com[comp_key]

    color = comp_data["color"]
    score = company_total_score(comp_data)

    # 헤더
    h1, h2 = st.columns([3,1])
    with h1:
        st.markdown(f"### <span style='color:{color}'>{comp_data['corp']}</span> — {comp_data['seg']}", unsafe_allow_html=True)
    with h2:
        st.metric("종합 점수", f"{score}점")

    # 수출 시그널
    st.markdown("#### 완제품 수출 시그널")
    exp_cols = st.columns(len(comp_data["export_signals"]))
    for i, (sname, sig) in enumerate(comp_data["export_signals"].items()):
        with exp_cols[i]:
            st_val = sig.get("strength",0)
            bg = {"3":"#fff5f5","2":"#f0fff4","1":"#f0f7ff","0":"#f8f9fa"}.get(str(st_val),"#f8f9fa")
            border = sig_color(st_val)
            yoy = sig.get("latest_yoy","N/A")
            trend = sig.get("trend","")
            st.markdown(f"""
            <div style="background:{bg};border-left:4px solid {border};
                        border-radius:8px;padding:12px;margin:4px 0">
              <div style="font-size:0.85rem;font-weight:600">{sname}</div>
              <div style="font-size:1.3rem;margin:4px 0">{sig.get('signal','N/A')}</div>
              <div style="font-size:0.8rem">YoY: <b>{yoy}%</b>  {trend}</div>
              <div style="font-size:0.75rem;color:#666;margin-top:4px">{sig.get('description','')}</div>
            </div>
            """, unsafe_allow_html=True)

    # CAPEX 시그널
    if comp_data["capex_signal"]:
        st.markdown("#### 공급망 CAPEX 수입 시그널 (6~12개월 선행)")
        cap = comp_data["capex_signal"]
        st_val = cap.get("strength",0)
        c1, c2 = st.columns([2,1])
        with c1:
            st.markdown(f"""
            <div style="background:{{'3':'#fff5f5','2':'#f0fff4','1':'#f0f7ff','0':'#f8f9fa'}}.get(str({st_val}),'#f8f9fa');
                        border-left:4px solid {sig_color(st_val)};border-radius:8px;padding:16px">
              <div style="font-size:1rem;font-weight:600">{cap.get('rule_name','CAPEX')}</div>
              <div style="font-size:1.4rem;margin:8px 0">{cap.get('signal','N/A')}</div>
              <div>YoY: <b>{cap.get('latest_yoy','N/A')}%</b>  &nbsp;  {cap.get('trend','')}</div>
              <div style="font-size:0.85rem;color:#555;margin-top:8px">{cap.get('description','')}</div>
            </div>
            """, unsafe_allow_html=True)
        with c2:
            st.metric("3개월 평균 YoY", f"{cap.get('avg3_yoy','N/A')}%")
            st.metric("완료월", cap.get("latest_period","N/A"))

    # YoY 추이 차트 (수출 시그널 중 첫 번째)
    first_sig = next(iter(comp_data["export_signals"].values()), None)
    if first_sig and first_sig.get("yoy_history"):
        st.markdown("#### YoY 추이")
        hist = first_sig["yoy_history"]
        df_h = pd.DataFrame(hist, columns=["기간","YoY(%)"])
        fig = go.Figure()
        fig.add_bar(x=df_h["기간"], y=df_h["YoY(%)"],
                    marker_color=[sig_color(3) if v>=20 else sig_color(2) if v>=10
                                  else sig_color(1) if v>=0 else "#adb5bd"
                                  for v in df_h["YoY(%)"]], name="YoY")
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(height=280, margin=dict(t=10,b=10,l=0,r=0),
                          yaxis_title="YoY (%)", xaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)

    # 투자 판단 가이드
    with st.expander("💡 시그널 조합 해석 가이드"):
        guide_data = [
            ["수출 🔥 + CAPEX ✅","현재 출하↑, 미래 생산↑","적극 매수 검토"],
            ["수출 ✅ + CAPEX 🔥","현재 선방, 다음 사이클 대비","분할 매수 검토"],
            ["수출 ⚪ + CAPEX 🔥","다운사이클 바닥, CAPEX 시작","6~12개월 후 반전 대비"],
            ["수출 🔥 + CAPEX ⚪","피크아웃 주의","비중 축소 고려"],
        ]
        st.dataframe(
            pd.DataFrame(guide_data, columns=["시그널 조합","의미","투자 행동"]),
            use_container_width=True, hide_index=True,
        )
        st.caption("⚠️ 지역 필터는 완벽한 기업 구분이 아닙니다. 협력업체 물량 혼재 가능.")

# ════════════════════════════════════════════════════════
#  탭 4: 차트
# ════════════════════════════════════════════════════════
with tab_chart:
    st.subheader("📈 수출입 YoY 추이 비교")

    # 모든 시그널 목록
    all_sigs = {}
    for sector, data in nat.items():
        if tab_filter and sector not in tab_filter:
            continue
        for sname, sig in data["signals"].items():
            if sig.get("yoy_history"):
                all_sigs[f"[{sector}] {sname}"] = sig

    for company, data in com.items():
        for sname, sig in data["export_signals"].items():
            if sig.get("yoy_history"):
                all_sigs[f"[{data['corp']} {data['seg']}] {sname}"] = sig
        if data.get("capex_signal") and data["capex_signal"].get("yoy_history"):
            cap = data["capex_signal"]
            all_sigs[f"[{data['corp']} {data['seg']}] {cap.get('rule_name','CAPEX')}"] = cap

    selected = st.multiselect(
        "비교할 시그널 선택 (최대 5개)",
        list(all_sigs.keys()),
        default=list(all_sigs.keys())[:3],
        max_selections=5,
    )

    if selected:
        fig = go.Figure()
        colors = ["#1c83e1","#ff4b4b","#21c354","#ff9900","#9c36b5"]
        for i, key in enumerate(selected):
            sig  = all_sigs[key]
            hist = sig["yoy_history"]
            df_h = pd.DataFrame(hist, columns=["기간","YoY(%)"])
            fig.add_trace(go.Scatter(
                x=df_h["기간"], y=df_h["YoY(%)"],
                name=key, mode="lines+markers",
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=5),
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
        fig.update_layout(
            height=420,
            xaxis_title=None, yaxis_title="YoY (%)",
            legend=dict(orientation="h", y=-0.25),
            hovermode="x unified",
            margin=dict(t=20,b=60,l=0,r=0),
        )
        st.plotly_chart(fig, use_container_width=True)

        # 최신 수치 비교 테이블
        st.markdown("**최신 수치 비교**")
        compare_rows = []
        for key in selected:
            sig = all_sigs[key]
            compare_rows.append({
                "시그널": key,
                "최신YoY(%)":  sig.get("latest_yoy","N/A"),
                "전월YoY(%)":  sig.get("prev_yoy","N/A"),
                "3M평균(%)":   sig.get("avg3_yoy","N/A"),
                "추세":        sig.get("trend","N/A"),
                "판정":        sig.get("signal","N/A"),
                "완료월":      sig.get("latest_period","N/A"),
            })
        st.dataframe(pd.DataFrame(compare_rows), use_container_width=True, hide_index=True)
    else:
        st.info("위에서 시그널을 선택하세요.")

# ── 푸터 ──────────────────────────────────────────────────────────
st.divider()
gen_time = meta.get("generated","")
if gen_time:
    try:
        t = datetime.fromisoformat(gen_time).strftime("%Y-%m-%d %H:%M")
        st.caption(f"마지막 분석: {t}  |  분석기간: {meta['sp']} ~ {meta['ep']}")
    except Exception:
        pass
st.caption("데이터 출처: 관세청 수출입 무역통계 (공공데이터포털)  |  ⚠️ 투자 참고용, 매수·매도 권유 아님")
