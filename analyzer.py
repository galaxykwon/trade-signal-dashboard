"""
analyzer.py — 수출입 API 호출 + 시그널 분석 엔진
Streamlit Cloud에서 import하여 사용
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime
from dateutil.relativedelta import relativedelta

import requests
import pandas as pd

# ── API 엔드포인트 ─────────────────────────────────────────────────
URL_NATIONAL = "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
URL_REGION   = "https://apis.data.go.kr/1220000/SgguItemtradeService/getSgguItemtradeList"

# ── 시군구 코드 (관세청 체계) ─────────────────────────────────────
REGIONS = {
    "삼성_화성":     "31590",
    "삼성_평택":     "31220",
    "삼성_용인":     "31460",
    "하이닉스_이천": "31500",
    "하이닉스_청주": "33111",
}

# ══════════════════════════════════════════════════════════════════
#  섹터 & 기업 정의
# ══════════════════════════════════════════════════════════════════
NATIONAL_SECTORS = {
    "반도체": {
        "icon": "🔵",
        "signal_rules": {
            "DRAM 수출":      {"codes":["8542321010","8542321020","8542321090"], "direction":"수출","threshold":15, "desc":"서버·AI 수요 선행지표", "stocks":["SK하이닉스","삼성전자"]},
            "NAND 수출":      {"codes":["8542321030"],                           "direction":"수출","threshold":15, "desc":"SSD·스마트폰 낸드 업황", "stocks":["삼성전자","SK하이닉스"]},
            "HBM·MCP 수출":   {"codes":["8542323000"],                           "direction":"수출","threshold":20, "desc":"AI용 고대역폭메모리", "stocks":["SK하이닉스","삼성전자"]},
            "시스템반도체 수출":{"codes":["8542311000"],                          "direction":"수출","threshold":15, "desc":"파운드리·팹리스 수혜", "stocks":["DB하이텍","가온칩스"]},
        },
    },
    "반도체장비": {
        "icon": "⚙️",
        "signal_rules": {
            "장비 수입 (CAPEX)": {"codes":["8486201000","8486204000","8486206020","8486209200","8486209310","8486402010"], "direction":"수입","threshold":20, "desc":"팹 설비투자 확대 선행 6~12개월", "stocks":["한미반도체","HPSP","주성엔지니어링"]},
        },
    },
    "전력기기": {
        "icon": "⚡",
        "signal_rules": {
            "초고압 변압기 수출": {"codes":["8504230000"],                                    "direction":"수출","threshold":20, "desc":"북미 AI 데이터센터·그리드 교체", "stocks":["HD현대일렉트릭","효성중공업"]},
            "중형 변압기 수출":   {"codes":["8504229010","8504229020","8504229030"],          "direction":"수출","threshold":20, "desc":"배전망·산업단지 인프라", "stocks":["제룡전기","LS ELECTRIC"]},
            "차단기·개폐기 수출": {"codes":["8535291000","8535292000","8535303000"],          "direction":"수출","threshold":20, "desc":"전력망 보호장치", "stocks":["LS ELECTRIC","HD현대일렉트릭"]},
            "ESS 배터리 수출":    {"codes":["8507802000","8507803000","8507809000"],          "direction":"수출","threshold":25, "desc":"재생에너지 연계 ESS", "stocks":["LG에너지솔루션","삼성SDI"]},
            "고압 전선 수출":     {"codes":["8544601010","8544601090"],                       "direction":"수출","threshold":20, "desc":"해상풍력·HVDC 케이블", "stocks":["대한전선","가온전선"]},
        },
    },
    "석유화학": {
        "icon": "🛢️",
        "signal_rules": {
            "나프타 수입단가":   {"codes":["2710114000","2710119000"], "direction":"수입","threshold":-10, "desc":"원가↓→마진 개선", "stocks":["롯데케미칼","LG화학"]},
            "PE 수출":          {"codes":["3901101000","3901201000"], "direction":"수출","threshold":10,  "desc":"포장재·산업용 플라스틱", "stocks":["LG화학","롯데케미칼"]},
            "PP 수출":          {"codes":["3902100000"],              "direction":"수출","threshold":10,  "desc":"자동차 내외장재 소재", "stocks":["대한유화","효성화학"]},
        },
    },
    "조선": {
        "icon": "🚢",
        "signal_rules": {
            "탱커·LNG선 수출":    {"codes":["8901200000"],            "direction":"수출","threshold":15, "desc":"고부가 선박 인도 지표", "stocks":["HD한국조선해양","삼성중공업","한화오션"]},
            "화물·컨테이너선 수출":{"codes":["8901901000"],           "direction":"수출","threshold":15, "desc":"벌크·컨테이너선 인도", "stocks":["HD현대미포","한화오션"]},
            "후판 수입 (건조선행)":{"codes":["7208511000","7208519000"],"direction":"수입","threshold":15, "desc":"야드 철판 소모↑=건조 확대", "stocks":["현대제철"]},
        },
    },
    "로봇": {
        "icon": "🤖",
        "signal_rules": {
            "산업용 로봇 수출":    {"codes":["8479501000","8479509000"], "direction":"수출","threshold":20, "desc":"제조·물류 자동화 글로벌 진출", "stocks":["두산로보틱스","레인보우로보틱스"]},
            "감속기 수입 감소":    {"codes":["8483409010","8483409020"], "direction":"수입","threshold":-10,"desc":"일본산 의존도↓→국산화", "stocks":["에스피지","에스비비테크"]},
        },
    },
    "화장품": {
        "icon": "💄",
        "signal_rules": {
            "기초화장품 수출": {"codes":["3304991000","3304992000"], "direction":"수출","threshold":15, "desc":"K-스킨케어 글로벌 ODM", "stocks":["코스맥스","한국콜마","에이피알"]},
            "색조화장품 수출": {"codes":["3304911000","3304101000"], "direction":"수출","threshold":15, "desc":"인디 브랜드 북미·일본 강세", "stocks":["씨앤씨인터내셔널","아이패밀리에스씨"]},
        },
    },
}

COMPANY_SECTORS = {
    "삼성전자_메모리": {
        "corp":"삼성전자","seg":"메모리","color":"#1428A0",
        "export_regions":["삼성_화성","삼성_평택"],
        "capex_regions": ["삼성_화성","삼성_평택"],
        "export_rules": {
            "DRAM 수출":  {"codes":["8542321010","8473304060"],"threshold":15,"desc":"DDR5·서버 DRAM 출하"},
            "HBM 수출":   {"codes":["8542323000"],             "threshold":20,"desc":"HBM3E — 엔비디아 AI GPU 탑재"},
            "NAND 수출":  {"codes":["8542321030"],             "threshold":15,"desc":"V-NAND — SSD·스마트폰"},
        },
        "capex_rule": {"name":"메모리 장비 CAPEX","codes":["8486201000","8486209200","8486206020","8486209310"],"threshold":20,"desc":"평택·화성 장비 반입↑ → 12개월 선행"},
    },
    "삼성전자_비메모리": {
        "corp":"삼성전자","seg":"비메모리","color":"#1428A0",
        "export_regions":["삼성_화성","삼성_용인"],
        "capex_regions": [],
        "export_rules": {
            "시스템반도체 수출": {"codes":["8542311000","8542399000"],"threshold":15,"desc":"파운드리·LSI — 퀄컴·구글 수주"},
            "이미지센서 수출":   {"codes":["8542391000"],             "threshold":15,"desc":"ISOCELL — 스마트폰 카메라"},
        },
        "capex_rule": None,
    },
    "SK하이닉스_메모리": {
        "corp":"SK하이닉스","seg":"메모리","color":"#EA002C",
        "export_regions":["하이닉스_이천","하이닉스_청주"],
        "capex_regions": ["하이닉스_이천","하이닉스_청주"],
        "export_rules": {
            "DRAM 수출": {"codes":["8542321010","8473304060"],"threshold":15,"desc":"이천 DRAM — DDR5·서버"},
            "HBM 수출":  {"codes":["8542323000"],             "threshold":30,"desc":"HBM3E — 엔비디아 블랙웰 (세계 1위)"},
            "NAND 수출": {"codes":["8542321030"],             "threshold":15,"desc":"청주 M15 낸드 — eSSD·스마트폰"},
        },
        "capex_rule": {"name":"HBM CAPEX 수입","codes":["8486201000","8486402010","8486209310"],"threshold":25,"desc":"본딩장비↑ → HBM 증산 6~12개월 선행"},
    },
    "SK하이닉스_비메모리": {
        "corp":"SK하이닉스","seg":"비메모리","color":"#EA002C",
        "export_regions":["하이닉스_이천"],
        "capex_regions": [],
        "export_rules": {
            "비메모리 수출": {"codes":["8542311000"],"threshold":15,"desc":"파운드리 자회사 물량 (참고용)"},
        },
        "capex_rule": None,
    },
}

# ══════════════════════════════════════════════════════════════════
#  API 호출
# ══════════════════════════════════════════════════════════════════
def _parse_period(it: dict) -> str | None:
    v = str(it.get("year","") or it.get("period","")).strip()
    v = v.replace(".","").replace("-","")
    d = "".join(c for c in v if c.isdigit())
    if len(d) == 6: return d
    yr = "".join(c for c in str(it.get("year","")) if c.isdigit())[:4]
    mo = "".join(c for c in str(it.get("month","")) if c.isdigit()).zfill(2)
    return yr+mo if len(yr)==4 and len(mo)==2 else None

def _call_api(url: str, params: dict) -> list:
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        text = r.text.strip()
        if text.startswith("<"):
            root = ET.fromstring(text)
            rc = root.find(".//resultCode")
            if rc is not None and rc.text != "00":
                return []
            return [{c.tag: c.text for c in item} for item in root.findall(".//item")]
        else:
            raw = r.json().get("response",{}).get("body",{}).get("items",{}).get("item",[])
            return [raw] if isinstance(raw, dict) else (raw or [])
    except Exception:
        return []

def fetch_national(api_key: str, hs_code: str, start: str, end: str) -> pd.DataFrame:
    start_dt = datetime.strptime(start, "%Y%m")
    end_dt   = datetime.strptime(end,   "%Y%m")
    rows, cur = [], start_dt
    while cur <= end_dt:
        chunk = min(cur + relativedelta(months=11), end_dt)
        items = _call_api(URL_NATIONAL, {
            "serviceKey": api_key, "type":"json",
            "hsSgn": hs_code,
            "strtYymm": cur.strftime("%Y%m"),
            "endYymm":  chunk.strftime("%Y%m"),
            "tradeType": "0",
        })
        for it in items:
            p = _parse_period(it)
            if p:
                rows.append({
                    "period":       pd.to_datetime(p, format="%Y%m"),
                    "export_value": float(it.get("expDlr") or 0),
                    "import_value": float(it.get("impDlr") or 0),
                })
        cur = chunk + relativedelta(months=1)
        time.sleep(0.15)
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=["period"]).sort_values("period").reset_index(drop=True)

def fetch_region(api_key: str, hs_code: str, sggu_cd: str, start: str, end: str) -> pd.DataFrame:
    """시군구 API — 서버 오류 시 전국 API로 자동 대체"""
    test = _call_api(URL_REGION, {
        "serviceKey": api_key, "type":"json",
        "hsSgn": hs_code, "sgguCd": sggu_cd,
        "strtYymm": start, "endYymm": start,
        "tradeType": "0",
    })
    if not test:
        return fetch_national(api_key, hs_code, start, end)

    start_dt = datetime.strptime(start, "%Y%m")
    end_dt   = datetime.strptime(end,   "%Y%m")
    rows, cur = list(), start_dt
    while cur <= end_dt:
        chunk = min(cur + relativedelta(months=11), end_dt)
        items = _call_api(URL_REGION, {
            "serviceKey": api_key, "type":"json",
            "hsSgn": hs_code, "sgguCd": sggu_cd,
            "strtYymm": cur.strftime("%Y%m"),
            "endYymm":  chunk.strftime("%Y%m"),
            "tradeType": "0",
        })
        for it in items:
            p = _parse_period(it)
            if p:
                rows.append({
                    "period":       pd.to_datetime(p, format="%Y%m"),
                    "export_value": float(it.get("expDlr") or 0),
                    "import_value": float(it.get("impDlr") or 0),
                })
        cur = chunk + relativedelta(months=1)
        time.sleep(0.2)
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=["period"]).sort_values("period").reset_index(drop=True)

def pool(dfs: list) -> pd.DataFrame:
    if not dfs: return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    cols = [c for c in ["export_value","import_value"] if c in combined.columns]
    return combined.groupby("period", as_index=False).agg({c:"sum" for c in cols}).sort_values("period").reset_index(drop=True)

# ══════════════════════════════════════════════════════════════════
#  시그널 평가
# ══════════════════════════════════════════════════════════════════
def calc_yoy(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df = df.copy().sort_values("period")
    df["yoy_pct"] = df[col].pct_change(12) * 100
    df["mom_pct"] = df[col].pct_change(1)  * 100
    return df

def evaluate(df: pd.DataFrame, direction: str, threshold: float) -> dict:
    base = {"signal":"데이터 없음","strength":0,"latest_yoy":None,"trend":"N/A",
            "yoy_history":[], "value_history":[]}
    if df.empty: return base
    col  = "export_value" if direction=="수출" else "import_value"
    df_c = calc_yoy(df, col)
    cur_ym = datetime.today().strftime("%Y-%m")
    if not df_c.empty and df_c["period"].iloc[-1].strftime("%Y-%m") == cur_ym:
        df_c = df_c.iloc[:-1]
    df_v = df_c.dropna(subset=["yoy_pct"])
    if len(df_v) < 4: return {**base,"signal":"데이터 부족"}

    latest = df_v["yoy_pct"].iloc[-1]
    prev   = df_v["yoy_pct"].iloc[-2]
    prev2  = df_v["yoy_pct"].iloc[-3]
    t3ago  = df_v["yoy_pct"].iloc[-4]
    avg3   = df_v["yoy_pct"].tail(3).mean()
    slope  = latest - prev2
    trend  = "상승 ↑" if slope > 3 else ("하락 ↓" if slope < -3 else "횡보 →")

    if threshold > 0:
        if avg3 >= threshold*1.5: sig, st = "🔥 강한 매수", 3
        elif avg3 >= threshold:   sig, st = "✅ 매수 시그널", 2
        elif avg3 >= threshold*.5:sig, st = "👀 주시 필요", 1
        else:                     sig, st = "⚪ 중립", 0
    else:
        if avg3 <= threshold*1.5: sig, st = "🔥 강한 매수", 3
        elif avg3 <= threshold:   sig, st = "✅ 매수 시그널", 2
        elif avg3 <= threshold*.5:sig, st = "👀 주시 필요", 1
        else:                     sig, st = "⚪ 중립", 0

    # 차트용 히스토리
    recent24 = df_v.tail(24)
    return {
        "signal": sig, "strength": st,
        "latest_yoy": round(latest,1), "prev_yoy": round(prev,1),
        "t3ago_yoy":  round(t3ago,1),  "avg3_yoy": round(avg3,1),
        "trend": trend,
        "latest_period": df_v["period"].iloc[-1].strftime("%Y-%m"),
        "latest_value":  round(df_v[col].iloc[-1]),
        "yoy_history":   list(zip(
            recent24["period"].dt.strftime("%Y-%m").tolist(),
            recent24["yoy_pct"].round(1).tolist()
        )),
        "value_history": list(zip(
            recent24["period"].dt.strftime("%Y-%m").tolist(),
            recent24["export_value"].round(0).tolist(),
            recent24["import_value"].round(0).tolist(),
        )),
    }

# ══════════════════════════════════════════════════════════════════
#  통합 분석 실행
# ══════════════════════════════════════════════════════════════════
def run_all(api_key: str, lookback_months: int = 24) -> dict:
    today    = datetime.today()
    end_dt   = today
    start_dt = end_dt - relativedelta(months=lookback_months)
    sp, ep   = start_dt.strftime("%Y%m"), end_dt.strftime("%Y%m")

    national_cache: dict = {}
    region_cache:   dict = {}
    results = {"national": {}, "company": {}, "meta": {"sp":sp,"ep":ep,"generated":datetime.now().isoformat()}}

    # Part A: 전국 섹터
    for sector, cfg in NATIONAL_SECTORS.items():
        results["national"][sector] = {"icon": cfg["icon"], "signals": {}}
        for sig_name, rule in cfg["signal_rules"].items():
            dfs = []
            for hs in rule["codes"]:
                if hs not in national_cache:
                    national_cache[hs] = fetch_national(api_key, hs, sp, ep)
                if not national_cache[hs].empty:
                    dfs.append(national_cache[hs])
            ev = evaluate(pool(dfs), rule["direction"], rule["threshold"])
            ev["description"] = rule["desc"]
            ev["direction"]   = rule["direction"]
            ev["stocks"]      = rule["stocks"]
            results["national"][sector]["signals"][sig_name] = ev

    # Part B: 핵심종목
    for company, cfg in COMPANY_SECTORS.items():
        results["company"][company] = {
            "corp":cfg["corp"],"seg":cfg["seg"],"color":cfg["color"],
            "export_signals":{}, "capex_signal":None,
        }
        exp_codes = [REGIONS[r] for r in cfg["export_regions"] if r in REGIONS]
        for sig_name, rule in cfg["export_rules"].items():
            dfs = []
            for hs in rule["codes"]:
                for rc in exp_codes:
                    key = (hs, rc)
                    if key not in region_cache:
                        region_cache[key] = fetch_region(api_key, hs, rc, sp, ep)
                    if not region_cache[key].empty:
                        dfs.append(region_cache[key])
            ev = evaluate(pool(dfs), "수출", rule["threshold"])
            ev["description"] = rule["desc"]
            results["company"][company]["export_signals"][sig_name] = ev

        if cfg["capex_rule"]:
            rule = cfg["capex_rule"]
            cap_codes = [REGIONS[r] for r in cfg["capex_regions"] if r in REGIONS]
            dfs = []
            for hs in rule["codes"]:
                for rc in cap_codes:
                    key = (hs, rc)
                    if key not in region_cache:
                        region_cache[key] = fetch_region(api_key, hs, rc, sp, ep)
                    if not region_cache[key].empty:
                        dfs.append(region_cache[key])
            ev = evaluate(pool(dfs), "수입", rule["threshold"])
            ev["description"] = rule["desc"]
            ev["rule_name"]   = rule["name"]
            results["company"][company]["capex_signal"] = ev

    return results

def sector_score(signals: dict) -> int:
    sigs = list(signals.values())
    if not sigs: return 0
    return round(sum(s.get("strength",0) for s in sigs) / (len(sigs)*3) * 100)
